import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { api, type UserRow } from "../api";
import s from "../admin.module.css";
import { day, num, phone } from "../format";
import { message, PageHead, Problem, Skeleton, useLoad } from "../ui";

type Shown = { state: "loading" } | { state: "shown"; numbers: string[] } | { state: "error"; error: string };

const STATUS: Record<string, string> = { active: "Signed up", trial: "Trial", suspended: "Suspended" };

/** Accounts, newest first. A number stays masked until someone asks for it, and that ask is in the audit log. */
export function UsersPage() {
  const [params, setParams] = useSearchParams();
  const addressQ = params.get("q") ?? "";
  const [q, setQ] = useState(addressQ);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => { setQ(addressQ); }, [addressQ]);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const typeQ = (v: string) => {
    setQ(v);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setParams(() => {
      const digits = v.replace(/\D/g, "");
      return digits ? new URLSearchParams({ q: digits }) : new URLSearchParams();
    }, { replace: true }), 300);
  };
  const load = useLoad(() => api.users(addressQ), addressQ);
  const [shown, setShown] = useState<Record<number, Shown>>({});

  const reveal = async (id: number) => {
    setShown((m) => ({ ...m, [id]: { state: "loading" } }));
    try {
      const { numbers } = await api.reveal(id);
      setShown((m) => ({ ...m, [id]: { state: "shown", numbers } }));
    } catch (e) {
      setShown((m) => ({ ...m, [id]: { state: "error", error: message(e) } }));
    }
  };
  const hide = (id: number) => setShown((m) => {
    const next = { ...m };
    delete next[id];
    return next;
  });

  const users = load.data?.users ?? [];
  return (
    <>
      <PageHead title="Users" lede="Accounts, newest first. Numbers stay masked; revealing one is written to the audit log.">
        <label className={s.field}>
          <span>Number has</span>
          <input className={s.input} type="search" inputMode="numeric" placeholder="any digits" value={q}
            onChange={(e) => typeQ(e.target.value)} aria-label="Search users by number" />
        </label>
      </PageHead>
      {load.error && <Problem error={load.error} retry={load.reload} />}
      {!load.data && !load.error && <Skeleton rows={5} />}
      {load.data && (
        <section className={`${s.card} ${load.loading ? s.stale : ""}`} aria-label="Users" aria-busy={load.loading}>
          <p className={s.label}>
            {users.length} {users.length === 1 ? "account" : "accounts"}{users.length >= 50 ? " · the newest 50" : ""}
            {addressQ ? ` with …${addressQ}` : ""}
          </p>
          {users.length ? (
            <div className={s.tableWrap}>
              <table className={`${s.table} ${s.stackTable}`}>
                <thead>
                  <tr>
                    <th scope="col">Number</th><th scope="col">Plan</th><th scope="col" className={s.num}>Balance</th>
                    <th scope="col" className={s.num}>Calls</th><th scope="col" className={s.num}>Minutes</th>
                    <th scope="col" className={s.num}>Documents</th><th scope="col" className={s.num}>Linked numbers</th>
                    <th scope="col">Joined</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((u) => <Row key={u.id} u={u} shown={shown[u.id]} reveal={reveal} hide={hide} />)}
                </tbody>
              </table>
            </div>
          ) : <p className={s.empty}>No account has a number with those digits.</p>}
        </section>
      )}
    </>
  );
}

function Row({ u, shown, reveal, hide }: { u: UserRow; shown: Shown | undefined; reveal: (id: number) => void;
  hide: (id: number) => void }) {
  return (
    <tr>
      <td data-label="Number" className={`${s.lead} ${s.who2}`}>
        {shown?.state === "shown" ? (
          <span className={s.revealed}>
            {shown.numbers.map((n, i) => (
              <span key={n} className={s.nowrap}>
                {phone(n)}{i > 0 ? <span className={s.muted}> · linked</span> : null}
              </span>
            ))}
          </span>
        ) : <span className={s.nowrap}>{u.phone}</span>}
        {shown?.state === "shown" ? (
          <button type="button" className={s.btnSm} onClick={() => hide(u.id)}>Hide</button>
        ) : (
          <button type="button" className={s.btnSm} onClick={() => reveal(u.id)} disabled={shown?.state === "loading"}
            aria-label={`Reveal the numbers of user ${u.id} (logged)`}>
            {shown?.state === "loading" ? "Revealing…" : "Reveal"}
          </button>
        )}
        {shown?.state === "error" && <span className={`${s.error} ${s.small}`} role="alert">{shown.error}</span>}
      </td>
      <td data-label="Plan">
        <span className={s.chip}>{STATUS[u.status] ?? u.status}</span>
        {!u.verified_at && u.status !== "active" ? <span className={`${s.muted} ${s.small}`}> not verified</span> : null}
      </td>
      <td data-label="Balance" className={s.num}>₹{num(u.credits)}</td>
      <td data-label="Calls" className={s.num}>{num(u.calls)}</td>
      <td data-label="Minutes" className={s.num}>{num(u.minutes)}</td>
      <td data-label="Documents" className={s.num}>{num(u.documents)}</td>
      <td data-label="Linked numbers" className={s.num}>{u.linked ? num(u.linked) : "–"}</td>
      <td data-label="Joined" className={s.nowrap}>{day(u.created_at)}</td>
    </tr>
  );
}
