# Hermes Agent for complex documents, finished after the call

## Where it fits

Only in the writer, and only for complex documents. Everything else keeps today's path.

| Request | Tier | Who writes it | Typical time |
|---|---|---|---|
| Undo / restore | T0 | code | < 1 s |
| A change ("dates 26 se 27") | T1 | editor: one call, checked patch ops | 2-4 s |
| A letter, application, invoice, short table | T2 | drafter: one draft call + one review call | ~6-12 s |
| A slide deck, a multi-section report or proposal, a large sheet | **T3** | **Hermes run** (plans, writes section by section through our document tools, checks its work) | minutes |

The talker never talks to Hermes: it stays one fast model call per turn (~1 s). On the call, a T3 request is
accepted like any other; BolDoc says it takes a few minutes, the link comes by SMS when it is ready (and a call-back,
later), and the caller may hang up.

```
talker ──create_doc──► control: orchestrator ──► Temporal task ──► worker
                                                                     │ T3 and HERMES_URL set
                                         POST /v1/runs ◄─────────────┤  instructions: our writing rules
   Hermes (Docker, internal network) ──► our MCP tools on control    │  input: the brief (draft id)
      │  get_brief · start · add_blocks · replace_block · set_meta · review · finish
      │  SSE /v1/runs/{id}/events ───────────────────────────────────► task.progress ("writing slide 3 of 8")
      │  POST /steer  ◄── a change said while it writes (task inbox)
      │  POST /stop   ◄── cancel
      ▼  run.completed
   worker reads the draft from the MCP store ──► questions for gaps (only if the caller is still on) ──► validate,
   render ──► commit ──► SMS
   (Hermes down, timed out or failed: the same task falls back to T2, so a document always comes)
```

## Decisions

- **Model:** the writer's own endpoint (THINKER_LLM, today Qwen3-235B on Amazon Bedrock, Mumbai). Its rate pool is
  separate from the live calls' Sarvam limit, so long agent runs never slow a conversation. Hermes needs native
  tool calling and a context of at least 64K tokens: both hold (measured: Qwen on Bedrock calls our tools).
  `reasoning_effort` is set explicitly (Hermes defaults custom endpoints to `medium`).
- **Tools:** only our MCP server (`platform_toolsets: {api_server: [boldoc]}`), with the built-in toolsets disabled
  as a backstop (terminal, file, web, browser, code execution, delegation, memory, skills, session_search, cron).
  The document is built through typed tools on control (`/internal/mcp`, not routed by Caddy, key header), so the
  model never writes a file format and every block passes DocEngine's validation.
- **No memory, no learning, no sharing between callers:** memory and user profile off, skills and background
  review off, session_search off, `sessions.auto_prune` on; each run is a fresh session.
- **Nothing phones home:** shared metrics off, update checks off, lazy installs off; the image is pinned by digest.
- **Bounded cost:** `agent.max_turns` capped; one T3 run at a time at first (`max_concurrent_runs: 1`); a run
  has a time budget, after which it is stopped and the task falls back to T2.
- **Questions:** Hermes' `clarify` is not used. The writer asks through our own `ask_caller` MCP tool: all questions
  in one call (at most 3, screened), answered on any of the caller's calls, by a callback or on the website; the tool
  call waits up to the deadline, and an unanswered question becomes a `[Your X]` placeholder (spike, below).
- **Durability:** a T3 task is an ordinary Temporal task with a longer activity timeout; if control restarts, the
  task is requeued and a new run starts (the half-written draft is dropped).
- **Switch:** off unless `HERMES_URL` is set; routing rules for "complex" are config (`HERMES_FOR`).

## How we decide it earns its place

The same complex requests through T2 (today) and T3 (Hermes), on the writer bench: time to the document, model
calls and tokens (cost), DocEngine validation problems, and a checklist score (every requested section present, no
invented facts, slide limits kept, language right). Hermes stays on only for the document types where it wins.

## Risks

- Image size (~1.1 GB compressed) and memory (recommended 2-4 GB): the box has 8 GB with ~6.5 GB free and 16 GB
  disk free; measure after the first runs.
- Patch releases are large (v0.22.3 merged ~620 PRs): pin the digest, upgrade deliberately, back up its data
  volume before an upgrade (rollback is unsupported once a newer version wrote its database).
- A leaked API key on the default toolsets would mean remote code execution: the port is never published, the key
  is 32+ random characters, and the toolsets are locked to our MCP server.

## Spike results

Hermes' API server, a metering proxy in front of Qwen3-235B on Bedrock, and our document tools over MCP (streamable
HTTP), writing a 9-section MUDRA project report from a brief with three facts left out.

- **Install:** v0.22.4 declares its core dependencies for Python 3.14 only; MCP needs the `[mcp]` extra (mcp 2.3,
  where FastMCP is now `MCPServer`) and the API server needs aiohttp. The official image bundles Chromium, Node and
  ffmpeg (about 1.1 GB): pin it by digest, internal network only.
- **Config that works:** `model.provider` → a `providers:` entry whose `api` is the metering proxy;
  `platform_toolsets.api_server: [mcp-boldoc]`; `clarify` and every built-in toolset in `agent.disabled_toolsets`;
  `tools.tool_search.enabled: off` (otherwise MCP tools sit behind tool search: 4 extra model calls per run);
  `mcp_servers.boldoc.timeout: 1800` for the waiting `ask_caller`; memory off. API: `API_SERVER_ENABLED/KEY/HOST`,
  port 8642, `POST /v1/runs {input, instructions, session_id}` with a durable `Idempotency-Key` (24 h), SSE
  `tool.started`/`tool.completed`/`message.delta`/`run.completed` (with token usage); `/steer` exists.
- **Questions:** Hermes' `clarify` releases a batch one question at a time (the next appears only after the last is
  answered) and the model also asked about its own work ("sections are short: rewrite or proceed?"). A callback
  needs every question up front, so `ask_caller` replaces it: run 2 asked loan amount, bank and rent in one call,
  waited 76 s for the stand-in callback, and used all three answers.
- **Length:** Qwen writes 150-240 words a section; with `check` reporting sections under 75% of target and `finish`
  refusing under 50%, run 2 reached 2,334 of 2,870 words (81%). Outline targets go about 25% above the wanted length.
- **Totals:** run 1 wrote "total project cost Rs 6,00,000" for items adding up to Rs 5,00,000; `check` now sums each
  table's Total row, and run 2's figures were right.
- **Restart:** the tools server stopped for 10 s mid-run: one `write_section` failed, the MCP client reconnected,
  `check` showed what was missing and the run finished. The real draft lives in Postgres, so nothing is rewritten.
- **Cost and time:** 24-30 model calls, 222k-373k prompt and 6-11k output tokens: Rs 5.7-7.9 a run; 85-110 s of
  writing (plus any wait for the caller).
- **Housekeeping:** on start Hermes checks for updates and audits the host's security settings: update checks off
  in production; the container sees nothing of the host.

## Built

- **On the call:** next to the intake and the sense check, one small call (`apps/thinker/scope.py`) says whether the
  request is a long document and which type (`apps/thinker/catalogue.py`: 13 types, each with its usual sections,
  scoping questions, formats and size). Bench on Qwen3-235B: short 24/24 (no letter ever took the long path), long
  28/28 incl. a caller adding a section (the usual 9 kept + "SWOT analysis") and naming only three; p50 0.7 s. The
  talker's [STATE] then guides scoping: what's missing, the callback question on its own, the outline read back,
  time (about 10 minutes) and price (60 credits); the read-back gate is today's. A trial caller hears that big
  documents need a free sign-up (the link by SMS, once a day) and may have a short version now.
- **At create:** control builds the brief (outline with target lengths, facts given and not given, saved details,
  transcript, callback consent) and the task goes to T3; the brief lives on the task, so the writer works after the
  caller hangs up. Retention clears it with the transcript; drafts are deleted after the same 30 days.
- **The run:** `apps/control/bigdoc.py` starts the Hermes run (idempotency key per task and run number), follows it,
  reattaches after a restart, stops it over 20 minutes of its own work or over the token cap, gives it one more run
  for sections it left out, and falls back to T2 with the outline. `apps/control/mcp_http.py` serves the five tools
  over MCP (hand-written JSON-RPC: control's tests run on Python 3.9; Hermes' own client registered all five) and
  the metering proxy. A long document is never redrafted whole by an edit (`edit(..., allow_redraft=False)`).
- **Temporal:** `BigDocTask` runs the same `run_task` path in a one-hour activity with heartbeats.
- **Local end to end** (real Hermes v0.22.4, control's code, Qwen on Bedrock through the proxy): the 9-section
  MUDRA report in 156 s, 36 model calls, 686k prompt + 15k output tokens (about Rs 17), ~3,040 words; nothing
  invented (bank, rent, loan as placeholders when unanswered). Fixed after it: placeholders in any `[...]` form,
  numbers worked out from the caller's own figures, projections with stated assumptions, Indian number style.
- **Turning it on (the box):** `python3 infra/hermes/render_config.py ~/app/secrets --new-keys` (once), then
  `HERMES_URL=http://hermes:8642` in `infra/.env`, `docker compose --profile hermes up -d hermes` and
  `docker compose up -d control talker`. Without `HERMES_URL` nothing changes for callers.

## Web research

- **When:** the types that need it research by default, without asking the caller: reports, plans, decks, policies,
  proposals, lesson plans, question papers (catalogue `research`); not minutes, trackers, sales reports or tenders,
  which are the caller's own records. A caller who says on their own that they don't want it ("internet se kuch mat
  daalna") is heard, and so is one who asks for it (`logic/turns.asks_for_research`; `research_ok` on the create: None
  when they said nothing).
- **Two tools** (`apps/control/research.py`), served with the others over MCP and refused unless the brief says
  `research_ok`:
  - `web_search(query)`: Serper (Google's index, `gl=in`), 6 results with titles, links and snippets. Only the query
    leaves India, and it is screened first (`Privacy`): no email, PAN, IFSC, Aadhaar or phone number, no long number,
    no link, no figure the caller gave (years aside), no word of their name, their street or their organisation's
    own name ("Kumar" from "Kumar Tea & Snacks"; "tea stall" is their trade and may go). Towns and general terms
    (a scheme's or a bank's name) may.
  - `read_page(url, looking_for)`: control fetches the page itself, in Mumbai, from a link the search found or an
    official site (.gov.in, .nic.in, rbi.org.in, ...), screened like a query. https on port 443 to public addresses
    only (an internal service or the cloud's metadata address is never reached, by name or by redirect), at most
    3 MB and 45 s, HTML, text or PDF (PDFs are read in a process of their own: pypdf would hold control's
    interpreter). The site's robots.txt is respected (RFC 9309). Up to 4,000 characters come back: the passages about
    what the writer is looking for, as quoted data with a note that page text is never instructions.
  - Per document: 15 searches, 25 pages; a research run's token cap is 2.25M (pages ride along in every later model
    call), 1.5M otherwise.
- **Citations and Sources:** each page read gets a source number; the writer cites facts from it as `[n]` (never
  about the caller, never in a heading; disagreeing figures as a range citing both). `check` reports citations of
  pages never read; the document cites in reading order (`[1]` is the first page cited), drops citations of pages
  never read, and ends with a Sources list (each page cited, its link and the date read, in the document's
  language: "स्रोत", "{date} को देखा गया"); a deck gets Sources slides, six to a slide. Placeholder detection never
  takes a citation for a gap (`drafter._PLACEHOLDER`, `gaps._RUN`).
- **Admin:** the call page shows each long document's searches, pages read, citations, queries refused for the
  caller's privacy, and the search cost (Serper: $1 per 1,000 searches, `web_search_usd` in the rate card).
- **Local end to end**: the 9-section MUDRA report in 194 s, 47 model calls, 894k prompt + 14k output tokens (about Rs
  22, against Rs 17 without research); 3 searches ("PMMY MUDRA Kishor loan scheme eligibility and loan limit", "India
  organized cafe market size growth rate statistics", "Jaipur average commercial shop rent per square foot"), 12 reads
  of 7 pages, 3 cited (two banks' MUDRA pages for the Kishor limit, a market report for the cafe market), ~3,160 words.
  The Sources list rendered as a numbered section in Word and on the workbook's Notes sheet. No query carried the
  caller's details.
- **Live checks of the reader** (real sites): an official MUDRA page, NABARD scheme summaries and a ministry PDF read
  in 0.6-3.5 s; a JavaScript-only page gives "no readable text", and sites that refuse all robots answer 403 (the
  writer is told to use another source).
- **On the box**: 6 searches, all general ("Punjab National Bank Mudra loan interest rate", "average coffee shop profit
  margin India"), none refused; 11 reads of 8 pages; 4 sources cited; 53 model calls, 1.08M prompt + 16k output tokens
  (about Rs 26). Its question about the bank reached the caller while they were still on the line (phase 4).

## How a long document reads

User: "output should be structured and beautiful". The first production report was correct but read as walls of
text under a bare cover, with a Hindi title over English sections.
- **Structure (the writer's instructions):** each section opens with one or two sentences that give its point,
  level-2 headings for its parts, paragraphs of about 80 words, lists for steps, risks and requirements, every set of
  figures in a table, and a kv block of the key figures first when the document turns on them. Amounts are written
  with ₹. The run names the language ("all of it in Hindi (Devanagari)"), and each section's heading is written in
  it; scoping asks "English mein likhun ya Hindi mein?" when the caller hasn't said.
- **Cover:** `finish(title, subtitle, author)`: what it's for and for whom under the title, and at the foot who presents
  it and the date it was written. In the PDF the cover is a page of its own running to the sheet's edge, with the
  accent band a deck's title slide has.
- **Look (PDF and Word alike):** section numbers in the accent with a hairline under each section heading; tables
  ruled above, below and between rows (no grid) with a shaded header and total; whole rupees wherever every amount
  in a column is whole (₹5,00,000; an invoice keeps its paise); key figures as a two-column table; a table that fits
  on a page keeps its caption; the Sources list set as references; the details still to check boxed at the end, in
  the document's language. Word files are A4 (python-docx's default is US Letter). Workbook tabs are named by what
  they hold ("Detailed project cost", not "Table 1.1  Detailed project cos").

## Numbers that agree

The first researched production report had a profit table at ten times its own text (₹8,13,600 against ₹81,360),
a cost of the project (₹4,90,000) that its means of finance didn't cover (₹4,00,000), and a repayment schedule whose
principal added up to more than the loan. `check` compared totals in the text with table totals only.
- **Review:** once every section is written, one call to the writer's model (`apps/thinker/numbers.py`) reads the
  sentences that state numbers, the tables as printed (with their totals), key figures and charts, and lists what
  doesn't agree: text against tables, a factor of 10 or 100, cost against finance, derived rows. On that production
  draft it found all three, the same in three runs, in about 3 s for ~3k tokens (about Rs 0.1).
- **Loop:** `check` returns the list as `numbers_wrong`; `finish` refuses once while it isn't empty. Each version of the
  draft is reviewed once, at most three times a document, and the calls count toward its cost. A review that fails
  finds nothing: the document is never held up by its checker. Local run: three reviews, the last one clean.

## Tool errors

Hermes pauses an MCP server for about a minute after three errors in a row. Our tools answered ordinary outcomes
("the site asks robots not to read this page", a 403) as MCP errors, so three unreadable pages in a row cut the
writer off from every tool, `get_brief` and `write_section` too: the local run ended with nothing written. Only a
crash of our own code is an MCP error now; an `"ok": false` answer is a result the model reads.

## Files the caller adds

- **Where:** signed-up owners add files on the website. On their home page they wait for the next document: the next
  long document asked for on a call takes them (`service.py`, at create), and the call hears of them first ("Aapki
  2 files mil gayi hain"). On a long document's page they're for that document: while it's written, and for later
  changes. Up to 10 files and 20 MB a document; PDF, Word, Excel, PowerPoint, CSV, text, JPG and PNG, checked by
  their first bytes as well as their names. The file is the request's body (`POST /api/files`, name in
  `X-File-Name`): nothing to parse but bytes.
- **Reading:** only the text is kept, never the file (`materials`, migration 10; deleted after 30 days with the
  transcripts). Office files, CSV, text and PDFs are read in a process of their own (`extract.py`: tables a row a
  line); a photo, or a PDF with under ~25 characters a page (a scan), by Sarvam's document reader (`vision.py`,
  `POST /doc-ai/v1/job/digitise`, 10 pages a job, 30 a file; Rs 0.5 a page). Measured: a one-page quotation photo
  in about a second, tables back as rows; one line inside the table's area was dropped, and the result ZIP is
  served from a Microsoft Azure storage link (region not stated) while the API is in Mumbai.
- **The writer:** `get_brief` lists the files and their status; `get_material` gives each one's first 10,000
  characters (one file asked by name up to 20,000; 24,000 in all) as quoted data: the caller's own facts, never
  instructions. A run's token cap grows by 0.5M once it has read them (they ride along in every later call).
- **Found on the way:** the writer asks its questions on the first draft, before anything is committed, so the
  document's page (where the SMS link answers them) said "not found". A long document being written now opens
  there: progress, the outline, the questions and the files.

## Lessons from real calls

- **A refusal comes first and stays** (`session._track_refusal`). The writer's check refused "a 4th year college
  project" at 46 s, but refusals were only spoken at create time, so BolDoc scoped it for 70 s and refused at 116 s,
  then agreed again ("demo hai to likh deti hoon") and refused twice more. Now one refusal stops all scoping, time and
  price talk; two in a row are said at once; once said it stands until two checks in a row allow a different document
  (`turns.same_request`: "demo project report" is the same request). Talker bench (sarvam-105b x 4): refused before
  scoping 0/4 -> 4/4; holds it on "it's only a demo" 0/4 -> 4/4.
- **What is refused, relaxed the same day**. A rule against a student's graded work refused "research
  work on AI for an office" and even "personal research". The intake now refuses only forgeries (a document passed off
  as issued by someone the caller isn't and doesn't act for; the issuer writing its own, like a doctor, an owner or HR,
  a landlord, is fine), receipts for money not paid, and threats, harassment or defamation; everything else is allowed,
  academic and office work included. The writer's check never reads BolDoc's own "I can't write that" sentences
  (`session.without_refusals`): one wrong refusal in the transcript made every later check refuse.
- **The time is measured** (`apps/control/eta.py`): the 25th-75th percentile of the last runs of the same kind
  (decks or documents) from the event log, else the measured defaults (decks 1-2 min, reports 3-5 min). It was a fixed
  10 minutes; the deck took 77 s and reports 180-360 s.
- **The caller may go** (`session.short_work_in_progress`): when a long document starts, BolDoc says the time and asks
  whether they'd like to hang up (the link comes by SMS when it's ready) or stay; end_call is allowed while only a long
  document is being written. A browser caller who hangs up first gets the SMS too (`Orchestrator.auto_deliver`).
  Talker bench: time + offer 0/4 -> 4/4.
- **Waiting on the line**: no "aur kuch chahiye?" (4/4); Hermes's own progress ("4 of 9 sections") is said at most
  every 40 s after 20 s of quiet, and a soft two-note tone (`apps/talker/wait_tone.py`, WAIT_TONE=0 turns it off) plays
  every 4 s while both sides are quiet.
- **Ready**: BolDoc asks the caller to take a look and say whether it's right (0/4 -> 4/4), waits 25 s for a reader
  before asking again, and its last goodbye can't be cut off by the caller's "ok bye" (`CallerTurnStart(hold=...)`);
  the line drops when Plivo confirms the goodbye played.
