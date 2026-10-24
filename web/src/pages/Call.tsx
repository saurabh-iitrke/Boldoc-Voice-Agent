import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { AfterCall } from "../components/AfterCall";
import { CallDock } from "../components/CallDock";
import { CallOrb, orbState, type OrbState } from "../components/CallOrb";
import { ConsoleBrand } from "../components/app/ConsoleShell";
import { FairCopy } from "../components/FairCopy";
import { HandsetIcon, MicIcon, MicOffIcon } from "../components/icons";
import { QrCall, hasQrFor } from "../components/site/QrCall";
import { StatusLine } from "../components/StatusLine";
import { TalkLanguage } from "../components/TalkLanguage";
import { API_MODE, ApiError, api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useVoiceLevel } from "../lib/audioLevel";
import { billedCost, callCost, type CallCost } from "../lib/callCost";
import { useDialIn } from "../lib/dialIn";
import { rupees } from "../lib/format";
import { useLiveSession } from "../lib/live";
import { MIC } from "../lib/mic";
import { usePlivoCall } from "../lib/plivoCall";
import { PRICES } from "../lib/site";
import { TALK_LANGUAGES, useTalkLanguage } from "../lib/talkLanguage";
import type { Format } from "../lib/types";
import { useMedia } from "../lib/useMedia";
import s from "./Call.module.css";

type Phase = "ready" | "starting" | "call";
type Problem = { title: string; message: string; code?: string; kind?: "mic" | "line" | "balance" };

function wsUrl(path: string): string {
  return `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${path}`;
}

class MicError extends Error {
  constructor(public why: "blocked" | "unsupported") { super(why); }
}

/** The microphone is asked for inside the click: a "no" is known before a call is placed. */
async function askMicrophone(): Promise<void> {
  if (!navigator.mediaDevices?.getUserMedia) throw new MicError("unsupported");
  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: MIC });
  } catch {
    throw new MicError("blocked");
  }
  stream.getTracks().forEach((t) => t.stop());  // the call opens its own; the permission is remembered
}

/** The refusals a signed-in browser call can meet, in our own words (the server's message is the fallback). */
const SETUP: Record<string, Problem> = {
  busy: { title: "All lines are busy", message: "BolDoc is on as many calls as it can take. Try again in a few minutes.",
    kind: "line" },
  no_credits: { title: "Your balance has run out", message: "Adding money is coming soon. Your documents stay in "
    + "your account.", kind: "balance" },
  trial_over: { title: "Your free calls are used", message: "Your documents stay in your account; see your balance "
    + "for what's next.", kind: "balance" },
};

/** What BolDoc is doing, in words, under its orb. */
const SAYS: Record<OrbState, [string, string]> = {
  wait: ["Connecting", "This takes a second or two."],
  speaking: ["BolDoc is speaking", "Talk any time: BolDoc stops and listens."],
  listening: ["Listening", "Say what you need, in your own words."],
  muted: ["You're muted", "Unmute when you want to talk."],
  thinking: ["Thinking", "A moment while BolDoc works out the reply."],
  writing: ["BolDoc is writing", "Your document appears as it's written."],
  ended: ["Call ended", ""],
};
const CHIPS: [string, OrbState[]][] = [["Listening", ["listening", "muted"]], ["Thinking", ["thinking"]],
  ["Speaking", ["speaking"]], ["Writing", ["writing"]]];

/** "Talk in browser" for a signed-in account (the route requires it): a start page before the call; during it, BolDoc's
 * state and the document as it's written; after it, the document, its cost, the next step and what was said (on
 * request). ?via=demo plays a recorded call instead.
 */
export function Call() {
  const [params] = useSearchParams();
  const demo = params.get("via") === "demo";
  const nav = useNavigate();
  const { me, setMe } = useAuth();
  const [phase, setPhase] = useState<Phase>(demo ? "call" : "ready");
  const [session, setSession] = useState<string | null>(null);
  const [simulated, setSimulated] = useState(false);  // mock mode: the recorded call stands in for Plivo
  const [setup, setSetup] = useState<Problem>();
  const [mic, setMic] = useState<MicError["why"]>();
  const [formats, setFormats] = useState<Format[]>([]);
  const sinceId = useRef<number | undefined>(undefined); // the account's newest ledger row when the call began
  const [lang, setLang, picked] = useTalkLanguage();
  const narrow = useMedia("(max-width: 860px)"); // keep in sync with Call.module.css

  const scripted = demo || simulated;
  const speed = Math.min(8, Math.max(0.25, Number(params.get("speed") ?? 1) || 1));
  const source = useMemo(() => (scripted ? { type: "demo" as const, speed } :
    session ? { type: "ws" as const, url: wsUrl(`/api/live?session=${encodeURIComponent(session)}`) } : null),
  [scripted, session, speed]);
  const { state, push, restart, stop } = useLiveSession(source);
  const call = usePlivoCall((st, reason) => push({ kind: "call", state: st, reason }));

  useEffect(() => {
    document.title = demo ? "A recorded call · BolDoc" : "Talk to BolDoc · BolDoc";
    if (demo) {  // "Watch a recorded call" from a problem page: same page, so start it here
      setSetup(undefined);
      setMic(undefined);
      setPhase("call");
    }
  }, [demo]);

  const begin = async () => {
    setPhase("starting");
    setSetup(undefined);
    setMic(undefined);
    try {
      if (API_MODE !== "mock") await askMicrophone();
      // the newest ledger row before the call: what comes after it is this call's charges (see billedCost)
      // none picked for this call: it starts in their last call's language (the server remembers it)
      const [t, before] = await Promise.all([api.browserToken(picked), api.credits().catch(() => undefined)]);
      sinceId.current = before ? (before.ledger[0]?.id ?? 0) : undefined;
      setSession(t.session);
      setPhase("call");
      if (API_MODE === "mock") { setSimulated(true); return; }
      // the session rides along as an X-PH header: the server ties this WebRTC call to it and streams it back here
      await call.start(t.token, t.destination, { "X-PH-Session": t.session });
    } catch (e) {
      setPhase("ready");
      if (e instanceof MicError) setMic(e.why);
      else if (e instanceof ApiError) {
        setSetup({ ...(SETUP[e.code ?? ""] ?? { title: "We couldn't start the call", message: e.message }), code: e.code });
      } else {
        setSetup({ title: "We couldn't start the call", message: "Check your connection and try again.", kind: "line" });
      }
    }
  };

  const live = state.call === "connected" || state.call === "reconnecting";
  const ended = state.call === "ended" || state.call === "failed";
  // the caller's level, written to --level on the screen's root: the orb and the control bar both read it
  const meterRef = useVoiceLevel(scripted ? "synthetic" : live && !call.muted ? "mic" : "off",
    scripted ? state.userSpeaking : true);

  // the running cost ticks with the clock while the line is open
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!live) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [live]);
  const cost = callCost(state, now);

  const latestDoc = state.latest ? state.docs[state.latest] : undefined;
  const docId = latestDoc?.id;
  useEffect(() => {  // the document's formats, so its downloads can be offered (the live feed carries only its text)
    if (!docId) return;
    let off = false;
    api.document(docId).then((d) => { if (!off) setFormats(d.formats); }).catch(() => { /* none offered */ });
    return () => { off = true; };
  }, [docId, state.latest]);

  // after the call, what the account was charged and the balance it left. The ledger is the record: the minutes are
  // settled once Plivo reports the call's length, a few seconds after it ends, so ask a few times (about 20 s in
  // all), then the page's estimate stands. A recorded call charged nothing.
  const [balanceNow, setBalanceNow] = useState<number>();
  const [billed, setBilled] = useState<CallCost>();
  useEffect(() => {
    if (!ended || demo) return;
    let off = false;
    let timer = 0;
    const waits = [2500, 4000, 6000, 8000];
    const done = () => api.me().then((m) => { if (!off && m) setMe(m); }).catch(() => { /* keeps what it had */ });
    const ask = (i: number) => {
      timer = window.setTimeout(() => {
        api.credits().then((c) => {
          if (off) return;
          setBalanceNow(c.balance);
          const b = sinceId.current === undefined ? undefined : billedCost(c, sinceId.current);
          if (b) setBilled(b);
          if (b || i + 1 === waits.length) void done();
          else ask(i + 1);
        }).catch(() => { if (!off && i + 1 < waits.length) ask(i + 1); });
      }, waits[i]);
    };
    ask(0);
    return () => { off = true; clearTimeout(timer); };
  }, [ended, demo, setMe]);

  const endCall = () => {
    if (scripted) stop(); else call.hangup();
    push({ kind: "call", state: "ended" });
  };
  const again = () => { if (demo) restart(); else nav(0); };

  const problem: Problem | undefined = mic === "blocked" || call.problem === "microphone"
    ? { title: "Microphone blocked", message: "BolDoc needs your microphone. Allow it for this site in your browser's "
      + "settings (the icon next to the address), then try again.", kind: "mic" }
    : mic === "unsupported"
      ? { title: "This browser can't make calls", message: "Open this page in Chrome, Safari or Firefox, or call from "
        + "your phone.", kind: "mic" }
      : setup ?? (call.problem === "network" ? { title: "The call dropped", message: "The network connection was lost.",
        kind: "line" }
        : call.problem ? { title: "The call didn't connect", message: "Try again in a moment, or call from your phone.",
          kind: "line" }
          : undefined);

  const inCall = phase === "call" && !problem;
  const orb = orbState(state.call, state.agent, call.muted);
  const talksIn = useMemo(() => {   // the call's language: what BolDoc last spoke in, else what was picked
    const said = [...state.captions].reverse().find((c) => c.speaker !== "you" && c.lang)?.lang;
    const code = TALK_LANGUAGES.some((l) => l.code === said) ? said : lang;
    return TALK_LANGUAGES.find((l) => l.code === code)?.name ?? "English";
  }, [state.captions, lang]);
  const latestEntry = state.versions.find((v) => v.version === state.latest);
  const writing = state.agent === "writing" || !!state.stage;

  // a call can make several documents: one tab each, the newest shown
  const tabs = state.order.length > 1 && (
    <div className={s.docTabs} role="tablist" aria-label="This call's documents">
      {state.order.map((id) => {
        const t = state.documents[id] ?? { docs: {}, versions: [], deliveries: [] };
        const d = t.latest ? t.docs[t.latest] : undefined;
        const on = id === state.current;
        return (
          <button key={id} type="button" role="tab" aria-selected={on} className={`${s.docTab} ${on ? s.docTabOn : ""}`}
            onClick={() => push({ kind: "show", doc_id: id })}>
            <span className={s.docTabTitle}>{d?.title || "New document"}</span>
            <span className={s.docTabMeta}>{t.latest ? `v${t.latest}` : "writing"}</span>
          </button>
        );
      })}
    </div>
  );
  const fair = (
    <>
      {tabs}
      <FairCopy docs={state.docs} latest={state.latest} versions={state.versions} deliveries={state.deliveries}
        writing={writing && (state.writingNew ? !state.latest : true)} stage={state.stage} docId={docId}
        formats={formats} canDownload={!scripted && API_MODE !== "mock"} />
    </>
  );

  return (
    <div className={s.screen} ref={meterRef}>
      <header className={`${s.header} ${inCall && !ended ? s.inCall : ""}`}>
        <ConsoleBrand className={s.brand} size={23} />
        {inCall && !ended && (
          <StatusLine call={state.call} muted={call.muted} connectedAt={state.connectedAt} endedAt={state.endedAt}
            demo={demo} />
        )}
        <div className={s.headEnd}>
          {inCall && !ended
            ? <span className={s.talksIn}>Talks in <strong>{talksIn}</strong></span>
            : <Link to="/app" className={s.navLink}>Back to your documents</Link>}
        </div>
      </header>

      {problem ? (
        <ProblemSheet problem={problem} onRetry={() => (setup?.code === "busy" ? begin() : nav(0))} />
      ) : phase !== "call" ? (
        <ReadySheet starting={phase === "starting"} onStart={begin} lang={lang} onLang={setLang}
          balance={me?.credits?.balance} />
      ) : ended ? (
        <AfterCall demo={demo} onAgain={again}
          seconds={state.connectedAt ? ((state.endedAt ?? Date.now()) - state.connectedAt) / 1000 : 0}
          doc={latestDoc ? { id: latestDoc.id, model: latestDoc, changes: latestEntry?.changes ?? [] } : undefined}
          others={state.order.filter((id) => id !== state.current).map((id) => {
            const t = state.documents[id];
            return { id, title: (t?.latest ? t.docs[t.latest]?.title : undefined) || "Document" };
          })}
          formats={formats} canDownload={!scripted && API_MODE !== "mock"} cost={billed ?? cost}
          balance={balanceNow} phoneMasked={me?.phone_masked}
          captions={state.captions} />
      ) : (
        <main className={s.desk}>
          <div className={s.left}>
            <section className={s.boldoc} aria-label="BolDoc">
              <CallOrb state={orb} size={narrow ? "sm" : "lg"} />
              <div className={s.boldocText}>
                <p className={s.boldocState} aria-live="polite">{SAYS[orb][0]}</p>
                {SAYS[orb][1] && (
                  <p className={s.boldocHint}>
                    {orb === "writing" && state.stage ? `${state.stage}…` : SAYS[orb][1]}
                  </p>
                )}
                {!narrow && (
                  <span className={s.chips} aria-hidden="true">
                    {CHIPS.map(([label, states]) => (
                      <span key={label} className={`${s.chip} ${states.includes(orb) ? s.chipOn : ""}`}>{label}</span>
                    ))}
                  </span>
                )}
              </div>
            </section>
            {/* a phone: the document under BolDoc's state, as on a computer beside it (no captions to share the
                screen with) */}
            {narrow && <div className={s.docInline}>{fair}</div>}
          </div>
          {!narrow && <div className={s.right}>{fair}</div>}
        </main>
      )}

      {inCall && !ended && (
        <footer className={s.footer}>
          <CallDock muted={call.muted} canControl={!demo && live} onMute={call.toggleMute} onDigit={call.dtmf}
            onEnd={endCall} meterLabel={demo ? "The caller's voice" : undefined}
            cost={!demo && state.connectedAt ? `${rupees(PRICES.minute)} a minute, ${rupees(cost.total)} so far`
              : undefined} />
        </footer>
      )}

    </div>
  );
}

/** A phone: the document over the call, closed with its button or Escape; the focus goes back to what opened it. */
function PhoneAlternative() {
  const line = useDialIn();
  const qr = hasQrFor(line.e164);
  return (
    <div className={s.alt}>
      {qr && <QrCall size={64} e164={line.e164} display={line.display} />}
      <p>Prefer your phone? {qr ? "Scan, or call" : "Call"}{" "}
        <a href={`tel:${line.e164}`}>{line.display}</a>. It's the same BolDoc.</p>
    </div>
  );
}

function ReadySheet({ starting, onStart, lang, onLang, balance }: {
  starting: boolean; onStart: () => void; lang: string; onLang: (code: string) => void; balance?: number;
}) {
  return (
    <main className={s.ready}>
      <section className={s.sheet} aria-labelledby="ready-title">
        <div className={s.titles}>
          <h1 id="ready-title" className={s.title}>Talk to BolDoc</h1>
          <p className={s.lede}>Say what you need written. The document appears on this screen while BolDoc writes it.</p>
        </div>
        <TalkLanguage variant="chips" value={lang} onChange={onLang} disabled={starting} />
        <p className={s.mic}>
          <MicIcon size={20} />
          <span>Your browser will ask to use the microphone. Choose <strong>Allow</strong>.</span>
        </p>
        <div className={s.startBlock}>
          <button type="button" className={s.start} onClick={onStart} disabled={starting} aria-busy={starting || undefined}>
            <HandsetIcon size={20} />{starting ? "Connecting" : "Start the call"}
          </button>
          <p className={s.note}>
            {rupees(PRICES.minute)} a minute from your balance{balance !== undefined ? ` of ${rupees(balance)}` : ""}.
            No audio is recorded; the text of the call is kept for 30 days (<Link to="/privacy">privacy</Link>).
          </p>
        </div>
        <PhoneAlternative />
      </section>

      <aside className={s.steps} aria-label="What happens on the call">
        <span className={s.ring} aria-hidden="true">
          <span className={s.ringWave} /><span className={`${s.ringWave} ${s.ringB}`} /><span className={`${s.ringWave} ${s.ringC}`} />
          <span className={s.ringCore}><MicIcon size={32} /></span>
        </span>
        <ol className={s.stepList}>
          <li><span className={s.stepNo}>1</span><span><strong>Say what you need.</strong> <span className={s.stepSub}>
            “3 din ki leave application, behen ki shaadi hai.”</span></span></li>
          <li><span className={s.stepNo}>2</span><span><strong>BolDoc reads it back</strong> <span className={s.stepSub}>
            and writes once you say yes. You watch it appear.</span></span></li>
          <li><span className={s.stepNo}>3</span><span><strong>Change anything by voice.</strong> <span className={s.stepSub}>
            Then download it, or send the link to your phone.</span></span></li>
        </ol>
      </aside>
    </main>
  );
}

function ProblemSheet({ problem, onRetry }: { problem: Problem; onRetry: () => void }) {
  const balance = problem.kind === "balance";
  return (
    <main className={s.centre}>
      <section className={`${s.sheet} ${s.problem}`} aria-labelledby="problem-title" role="alert">
        <span className={s.problemIcon} aria-hidden="true">
          {problem.kind === "mic" ? <MicOffIcon size={22} /> : <HandsetIcon size={22} />}
        </span>
        <div className={s.titles}>
          <h1 id="problem-title" className={s.title}>{problem.title}</h1>
          <p className={s.lede}>{problem.message}</p>
        </div>
        <div className={s.actions}>
          {balance ? <Link className={s.startLink} to="/app/balance">See your balance</Link>
            : <button type="button" className={s.start} onClick={onRetry}>Try again</button>}
          {!balance && <Link className={s.quietLink} to="/app/call?via=demo">Watch a recorded call</Link>}
        </div>
        {!balance && <PhoneAlternative />}  {/* a call from the phone draws on the same balance */}
      </section>
    </main>
  );
}
