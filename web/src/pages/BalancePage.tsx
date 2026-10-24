import { useEffect, useState } from "react";

import { ConsoleShell } from "../components/app/ConsoleShell";
import { ApiError, api } from "../lib/api";
import { formatIndian, rupees } from "../lib/format";
import { TRIAL } from "../lib/site";
import type { Credits, LedgerRow } from "../lib/types";
import s from "./BalancePage.module.css";

const PACKS = [99, 249, 499];

/** A line of the history in plain words: it's money, in ₹. */
function what(r: LedgerRow): string {
  switch (r.kind) {
    case "signup_bonus": return "Sign-up gift";
    case "admin_adjust": return r.reason ? `Added by BolDoc: ${r.reason}` : "Added by BolDoc";
    case "topup": return "Added with UPI";
    case "doc_create": return r.doc_title ? `New document: ${r.doc_title}` : "New document";
    case "doc_edit": return r.doc_title ? `Change: ${r.doc_title}` : "Change to a document";
    case "call_minutes": {
      const m = Number((r.reason ?? "").match(/(\d+)\s*min/)?.[1]);
      return m ? `Call, ${m} ${m === 1 ? "minute" : "minutes"}` : "Call";
    }
    default: return r.reason ?? r.kind;
  }
}

const when = (iso: string) =>
  new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });

export function BalancePage() {
  const [c, setC] = useState<Credits | null>(null);
  const [error, setError] = useState<string>();
  useEffect(() => {
    document.title = "Balance · BolDoc";
    api.credits().then(setC).catch((e) => setError(e instanceof ApiError ? e.message : "Couldn't load your balance."));
  }, []);

  return (
    <ConsoleShell>
      <h1 className={s.h1}>Balance</h1>
      <p className={s.lede}>Your balance pays for calls and documents. It doesn't expire.</p>
      {error && <p className={s.error} role="alert">{error}</p>}
      {c && (
        <>
          <div className={s.top}>
            <section className={s.slab} aria-labelledby="now-h">
              <h2 id="now-h" className={s.slabLabel}>Now</h2>
              <p className={s.big}><span className={s.rupee}>₹</span>{formatIndian(c.balance)}</p>
              <p className={s.slabNote}>Enough for about {c.docs_estimate} documents with short calls.</p>
              <div className={s.add}>
                <p className={s.slabNote}>Adding money with UPI is coming soon.</p>
                <ul className={s.packs} aria-label="Amounts to add, coming soon">
                  {PACKS.map((p) => <li key={p} aria-disabled="true">{rupees(p)}</li>)}
                </ul>
              </div>
            </section>

            <section className={s.card} aria-labelledby="cost-h">
              <h2 id="cost-h" className={s.h2}>What things cost</h2>
              <dl className={s.prices}>
                <div><dt>A minute on a call</dt><dd>{rupees(c.prices.minute)}</dd></div>
                <div><dt>A new document, in every format</dt><dd>{rupees(c.prices.document)}</dd></div>
                <div><dt>A change to a document</dt><dd>{rupees(c.prices.edit)}</dd></div>
                <div><dt>The SMS with the link</dt><dd>Free</dd></div>
              </dl>
              <p className={s.gift}>{rupees(TRIAL.signupCredits)} free when you sign up.</p>
            </section>
          </div>

          <section className={s.history} aria-labelledby="hist-h">
            <div className={s.historyHead}>
              <h2 id="hist-h" className={s.h2}>History</h2>
              <span className={s.note}>Newest first</span>
            </div>
            {c.ledger.length ? (
              <div className={s.tableBox}>
                <table className={s.table}>
                  <thead>
                    <tr><th scope="col">When</th><th scope="col">What</th><th scope="col" className={s.num}>Amount</th>
                      <th scope="col" className={s.num}>Balance after</th></tr>
                  </thead>
                  <tbody>
                    {c.ledger.map((r) => (
                      <tr key={r.id}>
                        <td className={s.when}>{when(r.at)}</td>
                        <td>{what(r)}</td>
                        <td className={`${s.num} ${s.amount} ${r.amount > 0 ? s.plus : ""}`}>{rupees(r.amount, true)}</td>
                        <td className={`${s.num} ${s.after}`}>{rupees(r.balance_after)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <p className={s.note}>Nothing yet. Your first call and document show here.</p>}
          </section>
        </>
      )}
    </ConsoleShell>
  );
}
