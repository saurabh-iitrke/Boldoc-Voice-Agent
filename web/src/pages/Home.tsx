import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { CallMe } from "../components/app/CallMe";
import { ConsoleShell } from "../components/app/ConsoleShell";
import { FilesPanel } from "../components/app/FilesPanel";
import { ChevronIcon, DocIcon, DownloadIcon, HandsetIcon, MicIcon, RingInIcon } from "../components/icons";
import { ApiError, api, isLiveDocument } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useDialIn } from "../lib/dialIn";
import { FORMAT_LABEL, formatIndian, relativeDay, rupees } from "../lib/format";
import { BIG_SAYING, PRICES, TRIAL, TRY_SAYING } from "../lib/site";
import type { DocSummary, Profile } from "../lib/types";
import s from "./Home.module.css";

/** "today, 9:42 pm", "yesterday, 9:42 pm", "on 20 Oct, 7:05 pm": the end of "Version 2, changed …". */
function sinceWhen(iso: string): string {
  const w = relativeDay(iso);
  return /^(Today|Yesterday)/.test(w) ? w.charAt(0).toLowerCase() + w.slice(1) : `on ${w}`;
}

export function Home() {
  const { me } = useAuth();
  const line = useDialIn();
  const [params, setParams] = useSearchParams();
  const [cameForCallMe] = useState(() => params.get("call") === "me");  // the landing's "Call me", through sign-in
  const [callMeOpen, setCallMeOpen] = useState(cameForCallMe);
  const [docs, setDocs] = useState<DocSummary[] | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [error, setError] = useState<string>();

  useEffect(() => {
    document.title = "Your documents · BolDoc";
    api.documents().then(setDocs).catch((e) => {
      setDocs([]);
      setError(e instanceof ApiError ? e.message : "We couldn't load your documents. Refresh to try again.");
    });
    api.profile().then(setProfile).catch(() => undefined);
    if (cameForCallMe) setParams((p) => { p.delete("call"); return p; }, { replace: true });  // a refresh doesn't reopen it
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const name = profile?.name?.split(" ")[0];
  const trial = me?.status !== "active";
  const steps = [
    { done: (docs?.length ?? 0) > 0, title: "Make your first document", body: "Start a call above and say what you need." },
    { done: Boolean(profile?.name), title: "Add your details", body: "Your name, designation and office go into every letter, so you never repeat them.", to: "/app/settings" },
    { done: (docs ?? []).some((d) => d.head_version > 1), title: "Change one by voice", body: "Call again and say what to change. The same link opens the new version." },
  ];
  const allDone = steps.every((x) => x.done);

  return (
    <ConsoleShell>
      <h1 className={s.hello}>{name ? `Namaste, ${name}.` : "Namaste."}</h1>
      <p className={s.lede}>Say what you need on a call. BolDoc writes it while you talk, and every document lands here.</p>

      <div className={s.top}>
        <section className={s.make} aria-labelledby="make-h">
          <div className={s.makeHead}>
            <h2 id="make-h" className={s.cardTitle}>Make a document</h2>
            <p className={s.makeNote}>Pick how you want to talk. The document is the same.</p>
          </div>
          <ul className={s.ways} data-ways>
            <li>
              <a href={`tel:${line.e164}`} className={s.way} data-way data-tone="ink">
                <span className={s.wayIcon}><HandsetIcon /></span>
                <span className={s.wayText}>
                  <span className={s.wayTitle}>Call BolDoc</span>
                  <span className={`${s.waySub} ${s.number}`}>{line.display}</span>
                </span>
                <span className={s.wayNote}>From <strong>{me?.phone_masked}</strong>. The link comes by SMS.</span>
                <span className={s.wayGo} data-primary>Call now</span>
                <span className={s.chev}><ChevronIcon /></span>
              </a>
            </li>
            <li>
              <Link to="/app/call" className={s.way} data-way data-tone="grey">
                <span className={s.wayIcon}><MicIcon /></span>
                <span className={s.wayText}>
                  <span className={s.wayTitle}>Talk in browser</span>
                  <span className={s.waySub}>This device's microphone</span>
                </span>
                <span className={s.wayNote}>The document appears on screen as BolDoc writes it.</span>
                <span className={s.wayGo}>Start talking</span>
                <span className={s.chev}><ChevronIcon /></span>
              </Link>
            </li>
            <li>
              <button type="button" className={s.way} data-way data-tone="voice" aria-expanded={callMeOpen}
                aria-controls="call-me" onClick={() => setCallMeOpen(true)}>
                <span className={s.wayIcon}><RingInIcon /></span>
                <span className={s.wayText}>
                  <span className={s.wayTitle}>Call me</span>
                  <span className={s.waySub}>BolDoc rings your phone</span>
                </span>
                <span className={s.wayNote}>Pick up and say what you need. You pick its language.</span>
                <span className={s.wayGo}>Ring my phone</span>
                <span className={s.chev}><ChevronIcon /></span>
              </button>
            </li>
          </ul>
          <CallMe open={callMeOpen} onClose={() => setCallMeOpen(false)} reveal={cameForCallMe} />
        </section>

        <section className={s.slab} aria-labelledby="bal-h">
          <h2 id="bal-h" className={s.slabLabel}>{trial ? "Free trial" : "Balance"}</h2>
          {trial ? (
            <>
              <p className={s.slabBig}>{me?.trial?.calls_left ?? 0}<span className={s.slabUnit}> calls left</span></p>
              <p className={s.slabNote}>{rupees(TRIAL.signupCredits)} is added the moment your number is verified.</p>
            </>
          ) : (
            <>
              <p className={s.slabBig}><span className={s.rupee}>₹</span>{formatIndian(me?.credits?.balance ?? 0)}</p>
              <p className={s.slabNote}>Enough for about {me?.credits?.docs_estimate ?? 0} documents with short calls.</p>
            </>
          )}
          <dl className={s.prices}>
            <div><dt>a call minute</dt><dd>{rupees(PRICES.minute)}</dd></div>
            <div><dt>a document</dt><dd>{rupees(PRICES.document)}</dd></div>
            <div><dt>a change</dt><dd>{rupees(PRICES.edit)}</dd></div>
          </dl>
          <p className={s.slabLinks}>
            <Link to="/app/balance">History</Link>
            <span>Adding money: coming soon</span>
          </p>
        </section>
      </div>

      <section className={s.section} aria-labelledby="say-h">
        <div className={s.sectionHead}>
          <h2 id="say-h" className={s.h2}>Try saying</h2>
          <p className={s.sectionNote}>In any Indian language. BolDoc asks for anything missing.</p>
        </div>
        <ul className={s.sayList}>
          {TRY_SAYING.map((t) => (
            <li key={t.say} className={s.sayItem}>
              <p className={s.say} lang={t.lang}>“{t.say}”</p>
              <p className={s.sayWhat}>{t.becomes}</p>
            </li>
          ))}
          <li className={`${s.sayItem} ${s.sayWide}`}>
            <p className={s.say} lang={BIG_SAYING.lang}>“{BIG_SAYING.say}”</p>
            <p className={s.sayWhat}>{BIG_SAYING.becomes}</p>
          </li>
        </ul>
      </section>

      <FilesPanel variant="home" />

      {!allDone && (
        <section className={s.section} aria-labelledby="start-h">
          <h2 id="start-h" className={s.h2}>Getting started</h2>
          <ol className={s.steps}>
            {steps.map((st) => (
              <li key={st.title} className={s.step} data-done={st.done}>
                <span className={s.tick} aria-hidden="true">{st.done ? "✓" : ""}</span>
                <div>
                  <p className={s.stepTitle}>
                    {st.to && !st.done ? <Link to={st.to}>{st.title}</Link> : st.title}
                    {st.done && <span className="visually-hidden"> (done)</span>}
                  </p>
                  <p className={s.muted}>{st.body}</p>
                </div>
              </li>
            ))}
          </ol>
        </section>
      )}

      <section aria-labelledby="docs-h" className={s.section}>
        <div className={s.sectionHead}>
          <h2 id="docs-h" className={s.h2}>Your documents</h2>
          {docs && docs.length > 0 && <p className={s.sectionNote}>Newest first. Open one for every version and its link.</p>}
        </div>
        {error && <p className={s.error} role="alert">{error}</p>}
        {docs === null && <p className={s.muted}>Loading…</p>}
        {docs?.length === 0 && !error && (
          <div className={s.empty}>
            <p className={s.emptyTitle}>No documents yet</p>
            <p className={s.muted}>
              Start a call above and say what you need. Each document you make shows up here, with every version
              and a link to share.
            </p>
          </div>
        )}
        {docs && docs.length > 0 && (
          <ul className={s.rows} data-docs>
            {docs.map((d) => (
              <li key={d.id} className={s.doc} data-doc>
                <span className={s.docIcon}><DocIcon /></span>
                {/* the whole card opens the document; its download buttons sit above that */}
                <div className={s.docMain}>
                  <Link to={`/app/documents/${d.id}`} className={s.docLink} lang={d.doc_lang}>{d.title}</Link>
                  {d.hint && <p className={s.hint} lang={d.doc_lang}>{d.hint}</p>}
                  <p className={s.meta}>
                    {d.head_version === 0 ? `Being written, started ${sinceWhen(d.updated_at)}`
                      : `Version ${d.head_version}, ${d.head_version > 1 ? "changed" : "made"} ${sinceWhen(d.updated_at)}`}
                  </p>
                </div>
                <div className={s.downloads}>
                  {d.head_version > 0 && d.formats.map((f) => {
                    const label = FORMAT_LABEL[f] ?? f;
                    return isLiveDocument() ? (
                      <a key={f} href={`/api/documents/${encodeURIComponent(d.id)}/download/${f}`} className={s.dl}
                         download aria-label={`Download ${d.title} as ${label}`}>
                        <DownloadIcon size={16} /> {label}
                      </a>
                    ) : (
                      <span key={f} className={s.dl} title="Downloads work on the live server">
                        <DownloadIcon size={16} /> {label}
                      </span>
                    );
                  })}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </ConsoleShell>
  );
}
