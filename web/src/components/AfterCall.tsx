import { useState } from "react";
import { Link } from "react-router-dom";

import { ApiError, api } from "../lib/api";
import type { CallCost } from "../lib/callCost";
import { useDialIn } from "../lib/dialIn";
import { FORMAT_LABEL, rupees } from "../lib/format";
import type { Caption } from "../lib/live";
import type { Doc, Format } from "../lib/types";
import s from "./AfterCall.module.css";
import { DocBlocks } from "./DocBlocks";
import { FilePreview } from "./FilePreview";
import { DownloadIcon, HandsetIcon, PhoneIcon } from "./icons";

/** A masked number never breaks across lines ("+91 99••• ••655"). */
const keep = (text: string) => text.replace(/ /g, " ");

/** "3 min 12 s", "45 s" */
export function duration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const m = Math.floor(s / 60);
  return m ? `${m} min ${s % 60} s` : `${s} s`;
}

type Props = {
  demo: boolean;                         // a recorded call was playing
  seconds: number;                       // how long the line was open
  doc?: { id: string; model: Doc; changes: string[] };  // the latest version this call wrote
  others?: { id: string; title: string }[];  // the call's other documents
  formats: Format[];                     // its formats, once the API has said (downloads)
  canDownload: boolean;
  cost: CallCost;
  balance?: number;                      // the account's balance as the server has it now
  phoneMasked?: string;                  // where "Send to my phone" sends the link
  captions: Caption[];
  onAgain: () => void;
};

/** After the call: what came of it (the document, its downloads, the link to the phone), what it cost, and the way to
 * call again. The recorded call says what it was and how to make a real one. */
export function AfterCall({ demo, seconds, doc, others = [], formats, canDownload, cost, balance, phoneMasked,
  captions, onAgain }: Props) {
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState<string>();
  const [showSaid, setShowSaid] = useState(false);
  const line = useDialIn();

  // a browser call sends no SMS by itself: the document is on screen and in the account; the phone gets it on request
  async function send() {
    if (!doc) return;
    setSending(true);
    try {
      const r = await api.sendToPhone(doc.id);
      setSent(keep(`Sent to your number ending ${r.sent_to.slice(-4)}.`));
    } catch (e) {
      setSent(e instanceof ApiError ? e.message : "That didn't send. Try again from the document.");
    } finally {
      setSending(false);
    }
  }

  const lede = demo ? "That was a recorded call. Talk to BolDoc yourself, here or from your phone."
    : `${duration(seconds)} with BolDoc. ${doc ? "Your document is ready." : "Nothing was written on this call."}`;

  const thumbFmt = formats.includes("pdf") ? "pdf" : formats[0] ?? "pdf";  // the real first page (FilePreview)
  return (
    <main className={s.page}>
      <section className={s.top}>
        <div className={s.titles}>
          <h1 className={s.title}>Call ended</h1>
          <p className={s.lede}>{lede}</p>
        </div>
        <div className={s.topActions}>
          {demo ? (
            <>
              <Link className={s.ink} to="/app/call"><HandsetIcon size={18} />Talk in browser</Link>
              <a className={s.line} href={`tel:${line.e164}`}><PhoneIcon size={17} />Call {line.display}</a>
              <button type="button" className={s.quiet} onClick={onAgain}>Play it again</button>
            </>
          ) : (
            <button type="button" className={s.ink} onClick={onAgain}><HandsetIcon size={18} />Call again</button>
          )}
        </div>
      </section>

      <div className={s.grid}>
        {doc ? (
          <section className={s.docCard} aria-labelledby="ended-doc-h">
            <Link className={s.thumb} to={`/app/documents/${doc.id}`} aria-label={`Open ${doc.model.title}`}>
              <span className={s.thumbInner}>
                {canDownload && formats.length > 0 ? (
                  <FilePreview firstPageOnly fmt={thumbFmt}
                    url={`/api/documents/${encodeURIComponent(doc.id)}/preview/${thumbFmt}?v=${doc.model.version}`}
                    label={`${doc.model.title}, first page`} />
                ) : <DocBlocks doc={doc.model} />}
              </span>
            </Link>
            <div className={s.docBody}>
              <div className={s.docTitles}>
                <h2 id="ended-doc-h" className={s.docTitle}>{doc.model.title}</h2>
                <p className={s.docMeta}>
                  Version {doc.model.version}{doc.model.version > 1 && doc.changes.length > 0
                    ? `: ${doc.changes.join("; ")}.` : "."}
                </p>
              </div>
              <div className={s.downloads}>
                {formats.map((f, i) => (canDownload ? (
                  <a key={f} className={i === 0 ? s.ink : s.line} download
                    href={`/api/documents/${encodeURIComponent(doc.id)}/download/${f}`}>
                    <DownloadIcon size={16} />{i === 0 ? `Download ${FORMAT_LABEL[f] ?? f}` : FORMAT_LABEL[f] ?? f}
                  </a>
                ) : (
                  <button key={f} type="button" className={i === 0 ? s.ink : s.line} disabled>
                    <DownloadIcon size={16} />{i === 0 ? `Download ${FORMAT_LABEL[f] ?? f}` : FORMAT_LABEL[f] ?? f}
                  </button>
                )))}
                <Link className={s.quiet} to={`/app/documents/${doc.id}`}>Open the document</Link>
              </div>
              {others.length > 0 && (
                <p className={s.alsoWrote}>This call also wrote {others.map((o, i) => (
                  <span key={o.id}>{i > 0 ? ", " : ""}<Link to={`/app/documents/${o.id}`}>{o.title}</Link></span>
                ))}.</p>
              )}
              {!demo && (
                <div className={s.send}>
                  {sent ? <p className={s.sent} role="status">{sent}</p> : (
                    <>
                      <button type="button" className={s.sendBtn} disabled={sending} aria-busy={sending || undefined}
                        onClick={() => void send()}>
                        <PhoneIcon size={16} />Send to my phone
                      </button>
                      <span className={s.sendNote}>
                        The link goes by SMS{phoneMasked ? <> to <span className={s.nowrap}>{phoneMasked}</span></> : ""}.
                      </span>
                    </>
                  )}
                </div>
              )}
            </div>
          </section>
        ) : (
          <section className={s.emptyCard} aria-label="No document">
            <p className={s.emptyLead}>Nothing was written on this call.</p>
            <p className={s.emptyText}>Call again and say what you need: a letter, an application, an invoice or a
              table, in English or any Indian language. BolDoc reads it back before it writes.</p>
          </section>
        )}

        {!demo && (
          <aside className={s.cost} aria-labelledby="cost-h">
            <h2 id="cost-h" className={s.costHead}>This call cost</h2>
            <div className={s.row}><span>{cost.minutes} {cost.minutes === 1 ? "minute" : "minutes"}</span>
              <span>{rupees(cost.minuteAmount)}</span></div>
            {cost.newDocs > 0 && (
              <div className={s.row}><span>{cost.newDocs === 1 ? "New document" : `${cost.newDocs} new documents`}</span>
                <span>{rupees(cost.docAmount)}</span></div>
            )}
            {cost.changes > 0 && (
              <div className={s.row}><span>{cost.changes === 1 ? "One change" : `${cost.changes} changes`}</span>
                <span>{rupees(cost.changeAmount)}</span></div>
            )}
            <div className={s.total}><span>Total</span><span className={s.totalFig}><span className={s.rupee}>₹</span>
              {rupees(cost.total).replace("₹", "")}</span></div>
            {balance !== undefined && <p className={s.balance}>Balance now: {rupees(balance)}</p>}
            <a className={s.help} href="mailto:support@boldoc.in?subject=A%20browser%20call">
              Something went wrong? Tell us about this call</a>
          </aside>
        )}
      </div>

      {captions.length > 0 && (
        <section className={s.said} aria-labelledby="said-after-h">
          <div className={s.saidHead}>
            <h2 id="said-after-h" className={s.saidTitle}>What was said</h2>
            <button type="button" className={s.toggle} aria-expanded={showSaid} onClick={() => setShowSaid((v) => !v)}>
              {showSaid ? "Hide the conversation" : "Show the conversation"}
            </button>
          </div>
          {!demo && <p className={s.saidNote}>The text of this call stays in your account for 30 days. Audio is never
            recorded.</p>}
          {showSaid && (
            <ol className={s.lines}>
              {captions.filter((c) => c.final).map((c) => (
                <li key={c.id} className={s.lineItem} data-who={c.speaker === "you" ? "you" : "boldoc"} lang={c.lang}>
                  <span className={s.who}>{c.speaker === "you" ? "You" : "BolDoc"}</span>
                  <span>{c.text}</span>
                </li>
              ))}
            </ol>
          )}
        </section>
      )}
    </main>
  );
}
