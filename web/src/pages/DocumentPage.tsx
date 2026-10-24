import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { Button } from "../components/Button";
import { ConsoleShell } from "../components/app/ConsoleShell";
import { FilesPanel } from "../components/app/FilesPanel";
import { DocBlocks } from "../components/DocBlocks";
import { FilePreview } from "../components/FilePreview";
import { CopyIcon, DownloadIcon } from "../components/icons";
import { SiteFooter } from "../components/site/SiteFooter";
import { SiteHeader } from "../components/site/SiteHeader";
import { Stamp } from "../components/Stamp";
import { ApiError, api, isLiveDocument } from "../lib/api";
import { useDialIn } from "../lib/dialIn";
import { FORMAT_LABEL, shortDateTime } from "../lib/format";
import type { DocDetail, DocQuestion, Writing } from "../lib/types";
import s from "./DocumentPage.module.css";

/** An SMS link that isn't open on this browser: nobody has opened it yet, or another device has. */
type Gate = { kind: "unclaimed" } | { kind: "locked"; sentTo?: string };

/** Private view (/documents/:id) and the public SMS link (/d/:token) share this layout. */
export function DocumentPage({ visibility }: { visibility: "private" | "public" }) {
  const { id = "", token = "" } = useParams();
  const key = visibility === "private" ? id : token;
  const [data, setData] = useState<DocDetail | null>(null);
  const [error, setError] = useState<string>();
  const [gate, setGate] = useState<Gate>();
  const [opening, setOpening] = useState(false);
  const [copied, setCopied] = useState(false);
  const line = useDialIn();  // signed in: the account's line; a visitor: the public number

  const shut = (e: unknown) => {
    if (e instanceof ApiError && e.code === "link_unclaimed") setGate({ kind: "unclaimed" });
    else if (e instanceof ApiError && e.code === "link_locked") {
      setGate({ kind: "locked", sentTo: typeof e.data.sent_to === "string" ? e.data.sent_to.slice(-4) : undefined });
    } else setError(e instanceof ApiError ? e.message : "This document couldn't be loaded.");
  };

  useEffect(() => {
    setData(null);
    setError(undefined);
    setGate(undefined);
    (visibility === "private" ? api.document(key) : api.publicDocument(key)).then(setData).catch(shut);
  }, [key, visibility]);

  async function openHere() {
    setOpening(true);
    try {
      setData(await api.openLink(key));
      setGate(undefined);
    } catch (e) {
      shut(e);
    } finally {
      setOpening(false);
    }
  }

  useEffect(() => { if (data) document.title = `${data.doc.title} · BolDoc`; }, [data]);

  const download = (fmt: string) => (!isLiveDocument() ? undefined
    : visibility === "public" ? `/api/d/${encodeURIComponent(key)}/download/${fmt}`
      : `/api/documents/${encodeURIComponent(key)}/download/${fmt}`);

  async function copyLink() {
    if (!data?.link) return;
    try {
      await navigator.clipboard.writeText(data.link);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard blocked: the link is still visible to select */ }
  }

  const content = (
    <>
      {gate?.kind === "unclaimed" && (
        <div className={s.focus}>
          <section className={s.focusCard} aria-labelledby="gate-h">
            <h1 id="gate-h" className={s.focusTitle}>Your document is ready</h1>
            <p className={s.focusLead}>This link opens on one phone or computer: the first one that opens it. Anywhere
              else, you sign in with the number it was sent to.</p>
            <div><Button size="lg" busy={opening} onClick={() => void openHere()}>Open it here</Button></div>
          </section>
        </div>
      )}
      {gate?.kind === "locked" && (
        <div className={s.focus}>
          <section className={s.focusCard} aria-labelledby="gate-h">
            <h1 id="gate-h" className={s.focusTitle}>This link is open on another phone or computer</h1>
            <p className={s.focusLead}>It works only where it was opened first. To open it here, sign in with the
              number it was sent to{gate.sentTo ? <> (ending <strong>{gate.sentTo}</strong>)</> : null}. All your
              documents are there too.</p>
            <div><Link className={s.inkBtn} to={`/sign-in?next=${encodeURIComponent(`/d/${key}`)}`}>Sign in</Link></div>
          </section>
        </div>
      )}
      {error && (
        <div className={s.focus}>
          <section className={s.focusCard} aria-labelledby="gate-h">
            <h1 id="gate-h" className={s.focusTitle}>
              {visibility === "public" ? "This link isn't available" : "Document not found"}
            </h1>
            <p className={s.focusLead}>{error}{visibility === "public"
              ? " If BolDoc sent you this link, call again and ask for the document to be sent once more." : ""}</p>
            {visibility === "public" ? (
              <div className={s.focusActions}>
                <a className={s.inkBtn} href={`tel:${line.e164}`}>Call {line.display}</a>
                <Link className={s.lineBtn} to="/sign-in?next=/app">Sign in</Link>
              </div>
            ) : <div><Link className={s.inkBtn} to="/app">Your documents</Link></div>}
          </section>
        </div>
      )}
      {!error && !gate && !data && (
        <div className={s.focus}><p className={s.loading} role="status"><span className={s.dot} />Opening your
          document…</p></div>
      )}

      {data?.writing && (
        <div className={s.layout}>
          <aside className={s.side}>
            <h1 className={s.title} lang={data.doc.lang}>{data.doc.title}</h1>
            <p className={s.version}>Being written, started {shortDateTime(data.writing.since)}</p>
            <Progress writing={data.writing} />
            {visibility === "private" && !!data.questions?.length && (
              <Questions docId={key} questions={data.questions}
                         onSaved={(questions) => setData({ ...data, questions })} />
            )}
            {visibility === "private" && <FilesPanel variant="doc" docId={key} writing />}
          </aside>
          <div className={`${s.sheet} ${s.pending}`}>
            <p className={s.coverLead}>What it will cover</p>
            <ol className={s.outline}>
              {data.writing.outline.map((t) => <li key={t}>{t}</li>)}
            </ol>
          </div>
        </div>
      )}

      {data && !data.writing && (
        <div className={s.layout}>
          <aside className={s.side}>
            <h1 className={s.title}>{data.doc.title}</h1>
            <p className={s.version}>Version {data.doc.version}{data.versions.at(-1) ? `, ${shortDateTime(data.versions.at(-1)!.at)}` : ""}</p>
            {data.delivery && <div className={s.stamp}><Stamp delivery={data.delivery} small /></div>}

            {visibility === "private" && !!data.questions?.length && (
              <Questions docId={key} questions={data.questions}
                         onSaved={(questions) => setData({ ...data, questions })} />
            )}

            <section className={s.block} aria-labelledby="dl-h">
              <h2 id="dl-h" className={s.h2}>Download</h2>
              <ul className={s.downloads}>
                {data.formats.map((f, i) => {
                  const href = download(f);
                  return (
                    <li key={f}>
                      {href ? (
                        <a href={href} className={`${s.dl} ${i === 0 ? s.primary : ""}`} download>
                          <DownloadIcon /> {FORMAT_LABEL[f]}
                        </a>
                      ) : (
                        <span className={`${s.dl} ${s.disabled}`} title="Downloads work on the live server">
                          <DownloadIcon /> {FORMAT_LABEL[f]}
                        </span>
                      )}
                    </li>
                  );
                })}
              </ul>
              {data.link && (
                <button type="button" className={s.copy} onClick={() => void copyLink()}>
                  <CopyIcon /> {copied ? "Link copied" : "Copy link"}
                </button>
              )}
              {data.expires_at && <p className={s.expiry}>The link works until {shortDateTime(data.expires_at)}.</p>}
            </section>

            {visibility === "private" && <Share docId={key} />}
            {visibility === "private" && data.long && <FilesPanel variant="doc" docId={key} />}

            {data.versions.length > 1 && (
              <section className={s.block} aria-labelledby="ver-h">
                <h2 id="ver-h" className={s.h2}>Versions</h2>
                <ol className={s.versions} reversed>
                  {[...data.versions].reverse().map((v) => (
                    <li key={v.version}>
                      <span className={s.vNum}>Version {v.version}</span>
                      <span className={s.vWhen}>{shortDateTime(v.at)}</span>
                      <span className={s.vWhat}>{v.changes.join("; ")}</span>
                    </li>
                  ))}
                </ol>
              </section>
            )}
          </aside>
          {isLiveDocument() ? (
            <Preview formats={data.formats} doc={data.doc}
                     url={(fmt) => `${visibility === "public" ? `/api/d/${encodeURIComponent(key)}`
                       : `/api/documents/${encodeURIComponent(key)}`}/preview/${fmt}?v=${data.doc.version}`} />
          ) : (
            <div className={s.sheet}>
              <DocBlocks doc={data.doc} />
            </div>
          )}
        </div>
      )}
    </>
  );

  // the console's page inside its shell; the SMS link inside the site's header and footer
  if (visibility === "private") return <ConsoleShell>{content}</ConsoleShell>;
  return (
    <>
      <SiteHeader />
      <main className={s.publicMain}>{content}</main>
      <SiteFooter />
    </>
  );
}


/** The document as each of its files looks when downloaded (components/FilePreview), one format at a time; the
 * document's text stays readable for screen readers. */
function Preview({ formats, doc, url }: { formats: string[]; doc: DocDetail["doc"]; url: (fmt: string) => string }) {
  const shown = formats.filter((f) => f !== "json");
  const [fmt, setFmt] = useState(shown[0] ?? "pdf");
  return (
    <section className={s.preview} aria-label="Preview">
      {shown.length > 1 && (
        <div className={s.formatTabs} role="tablist" aria-label="Format">
          {shown.map((f) => (
            <button key={f} type="button" role="tab" aria-selected={f === fmt}
                    className={`${s.formatTab} ${f === fmt ? s.formatTabOn : ""}`} onClick={() => setFmt(f)}>
              {FORMAT_LABEL[f] ?? f.toUpperCase()}
            </button>
          ))}
        </div>
      )}
      <FilePreview key={fmt} url={url(fmt)} fmt={fmt} label={`${doc.title}, ${FORMAT_LABEL[fmt] ?? fmt}`} />
      <div className="visually-hidden"><DocBlocks doc={doc} /></div>
    </section>
  );
}


/** How far a long document's writer is: the section it's on, a web search, or waiting for the owner's answers. */
function Progress({ writing }: { writing: Writing }) {
  const stage = writing.stage ?? "";
  const now = writing.waiting ? "Waiting for your answers below"
    : stage.startsWith("section") ? `Writing ${stage}`
      : stage === "web research" ? "Looking things up on the web" : "Getting started";
  return (
    <section className={s.block} aria-labelledby="w-h">
      <h2 id="w-h" className={s.h2}>Progress</h2>
      <p className={s.loading} role="status"><span className={s.dot} />{now}</p>
      <p className={s.expiry}>It opens here when it's ready, and the link comes to your phone by SMS.</p>
    </section>
  );
}

/** The owner's ways to pass a document on: by SMS to their own phone, or a new link (the old one stops). */
function Share({ docId }: { docId: string }) {
  const [busy, setBusy] = useState<"send" | "link">();
  const [said, setSaid] = useState<string>();
  const [error, setError] = useState<string>();
  const [link, setLink] = useState<string>();
  const [copied, setCopied] = useState(false);

  async function act(what: "send" | "link") {
    setBusy(what); setError(undefined); setSaid(undefined);
    try {
      if (what === "send") {
        const r = await api.sendToPhone(docId);
        setSaid(`Sent to your number ending ${r.sent_to.slice(-4)}.`);
      } else {
        const r = await api.newLink(docId);
        setLink(r.link);
        setSaid(r.stopped ? "The old link has stopped working." : undefined);
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "That didn't work. Try again.");
    } finally {
      setBusy(undefined);
    }
  }

  async function copy() {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard blocked: the link is shown to select */ }
  }

  return (
    <section className={s.block} aria-labelledby="share-h">
      <h2 id="share-h" className={s.h2}>Share</h2>
      <div className={s.shareActions}>
        <Button variant="secondary" busy={busy === "send"} onClick={() => void act("send")}>Send to my phone</Button>
        <Button variant="quiet" busy={busy === "link"} onClick={() => void act("link")}>New link</Button>
      </div>
      {link && (
        <div className={s.newLink}>
          <span className={s.linkText}>{link}</span>
          <button type="button" className={s.copy} onClick={() => void copy()}>
            <CopyIcon /> {copied ? "Link copied" : "Copy link"}
          </button>
          <p className={s.expiry}>It opens on the first phone or computer that opens it.</p>
        </div>
      )}
      {said && <p className={s.said} role="status">{said}</p>}
      {error && <p className={s.error} role="alert">{error}</p>}
    </section>
  );
}

/** A long document's writer needs a few facts the caller didn't give (the SMS link opens this page). What
 * they type here goes to the writer the way an answer on a call does; a blank one becomes a gap to fill later. */
function Questions({ docId, questions, onSaved }: {
  docId: string; questions: DocQuestion[]; onSaved: (q: DocQuestion[]) => void;
}) {
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string>();
  const open = questions.filter((q) => !q.answer);

  async function send() {
    const answers = Object.fromEntries(Object.entries(draft).filter(([, a]) => a.trim()));
    if (!Object.keys(answers).length) {
      setNote("Write an answer first.");
      return;
    }
    setBusy(true);
    setNote(undefined);
    try {
      onSaved((await api.answerQuestions(docId, answers)).questions);
      setDraft({});
      setNote("Sent. They go into your document now.");
    } catch (e) {
      setNote(e instanceof ApiError ? e.message : "Your answers couldn't be sent. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className={`${s.block} ${s.ask}`} aria-labelledby="q-h">
      <h2 id="q-h" className={s.h2}>Questions for your document</h2>
      <ol className={s.questions}>
        {questions.map((q, i) => (
          <li key={q.question} className={s.question}>
            <label className={s.qText} htmlFor={`q-${i}`}>{q.question}</label>
            {q.answer ? <p className={s.qAnswer}>{q.answer}</p> : (
              <textarea id={`q-${i}`} className={s.qInput} rows={2} maxLength={500} value={draft[q.question] ?? ""}
                        onChange={(e) => setDraft({ ...draft, [q.question]: e.target.value })} />
            )}
          </li>
        ))}
      </ol>
      {open.length > 0 && (
        <div className={s.qActions}>
          <button type="button" className={s.inkBtn} disabled={busy} onClick={() => void send()}>
            {busy ? "Sending…" : "Send answers"}
          </button>
          <span className={s.qHint}>Leave one blank and it shows as a gap you can fill later.</span>
        </div>
      )}
      {note && <p className={s.qNote} role="status">{note}</p>}
    </section>
  );
}
