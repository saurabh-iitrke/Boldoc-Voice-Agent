import { Link } from "react-router-dom";

import { api, type AuditEntry } from "../api";
import s from "../admin.module.css";
import { when } from "../format";
import { PageHead, Problem, Skeleton, Status, useLoad } from "../ui";

const ACTIONS: Record<string, string> = {
  login: "Signed in", login_failed: "Sign-in refused", logout: "Signed out", reveal_number: "Revealed a number",
  view_transcript: "Opened a transcript", create_admin: "Created an admin", set_password: "Changed a password",
  reset_totp: "Reset an authenticator", disable: "Disabled an admin",
  agent_publish: "Published agent settings", agent_restore: "Restored agent settings",
};
const WHY: Record<string, string> = { unknown_user: "unknown username", password: "wrong password",
  code: "wrong or reused code" };

/** Who did what in the console: sign-ins (and refused ones), revealed numbers, opened transcripts, the server CLI. */
export function AuditPage() {
  const load = useLoad(() => api.audit(), "audit");
  const entries = load.data?.entries ?? [];
  return (
    <>
      <PageHead title="Audit log" lede="Every sign-in, refused sign-in, revealed number and opened transcript, and what
        was done on the server. Newest first." />
      {load.error && <Problem error={load.error} retry={load.reload} />}
      {!load.data && !load.error && <Skeleton rows={6} />}
      {load.data && (
        <section className={s.card} aria-label="Audit log">
          <p className={s.label}>The newest {entries.length} {entries.length === 1 ? "entry" : "entries"}</p>
          {entries.length ? (
            <div className={s.tableWrap}>
              <table className={`${s.table} ${s.stackTable}`}>
                <thead>
                  <tr><th scope="col">When</th><th scope="col">What</th><th scope="col">Who</th><th scope="col">On</th>
                    <th scope="col">From</th></tr>
                </thead>
                <tbody>{entries.map((e, i) => <Row key={`${e.at}-${i}`} e={e} />)}</tbody>
              </table>
            </div>
          ) : <p className={s.empty}>Nothing yet.</p>}
        </section>
      )}
    </>
  );
}

function target(e: AuditEntry) {
  if (!e.target) return <span className={s.muted}>–</span>;
  if (e.action === "view_transcript") return <Link className={s.rowLink} to={`/calls/${e.target}`}>call {e.target.slice(0, 8)}</Link>;
  if (e.target.startsWith("user:")) return <>user #{e.target.slice(5)}</>;
  return <>{e.target}</>;
}

function Row({ e }: { e: AuditEntry }) {
  const label = ACTIONS[e.action] ?? e.action.replace(/_/g, " ");
  const why = typeof e.detail?.why === "string" ? WHY[e.detail.why] ?? e.detail.why : null;
  return (
    <tr>
      <td data-label="When" className={`${s.lead} ${s.nowrap}`}>{when(e.at)}</td>
      <td data-label="What">
        {e.action === "login_failed" ? <Status ok={false}>{label}{why ? ` · ${why}` : ""}</Status> : label}
      </td>
      <td data-label="Who" className={s.nowrap}>{e.actor === "cli" ? "Server CLI" : e.actor}</td>
      <td data-label="On">{target(e)}</td>
      <td data-label="From" className={s.mono}>{e.ip ?? "–"}</td>
    </tr>
  );
}
