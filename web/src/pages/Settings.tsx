import { useEffect, useState, type FormEvent } from "react";

import { Button } from "../components/Button";
import { ConsoleShell } from "../components/app/ConsoleShell";
import { YourNumbers } from "../components/app/YourNumbers";
import { ApiError, api } from "../lib/api";
import { LANGUAGES } from "../lib/site";
import type { Profile } from "../lib/types";
import s from "./Account.module.css";

const EMPTY: Profile = { name: "", designation: "", organisation: "", address: "", default_lang: "", ui_lang: null,
  retention_days: 30 };

export function Settings() {
  const [p, setP] = useState<Profile | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string>();
  useEffect(() => {
    document.title = "My details · BolDoc";
    api.profile().then((x) => setP({ ...EMPTY, ...x })).catch(() => setP(EMPTY));
  }, []);

  const set = (k: keyof Profile) => (e: { target: { value: string } }) => {
    setSaved(false);
    setP((cur) => (cur ? { ...cur, [k]: e.target.value } : cur));
  };

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!p) return;
    setBusy(true);
    setError(undefined);
    try {
      setP({ ...EMPTY, ...(await api.saveProfile({ ...p, email: undefined })) });
      setSaved(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <ConsoleShell>
      <h1 className={s.h1}>My details</h1>
      <p className={s.lede}>
        BolDoc uses these when it writes for you, so you never have to spell your name or address on a call again.
        Only what you fill in is used.
      </p>
      <div className={s.settings}>
        {p && (
          <section className={s.panel} aria-label="Your details">
            <form className={s.form} onSubmit={save}>
              <label className={s.field}><span>Full name</span>
                <input value={p.name ?? ""} onChange={set("name")} autoComplete="name" maxLength={120} /></label>
              <label className={s.field}><span>Designation</span>
                <input value={p.designation ?? ""} onChange={set("designation")} autoComplete="organization-title"
                       placeholder="e.g. Senior Engineer" maxLength={120} /></label>
              <label className={s.field}><span>Organisation</span>
                <input value={p.organisation ?? ""} onChange={set("organisation")} autoComplete="organization"
                       maxLength={160} /></label>
              <label className={s.field}><span>Usual document language</span>
                <select value={p.default_lang ?? ""} onChange={set("default_lang")}>
                  <option value="">Ask me on the call</option>
                  {LANGUAGES.map((l) => <option key={l.code} value={l.code}>{l.name}</option>)}
                </select></label>
              <label className={`${s.field} ${s.wide}`}><span>Address</span>
                <textarea value={p.address ?? ""} onChange={set("address")} rows={3} autoComplete="street-address"
                          maxLength={300} /></label>
              <fieldset className={`${s.field} ${s.wide}`}>
                <legend>Conversation text</legend>
                <label className={s.radio}><input type="radio" name="ret" checked={p.retention_days !== 0}
                       onChange={() => setP({ ...p, retention_days: 30 })} /> Keep it 30 days, so I can see what was said</label>
                <label className={s.radio}><input type="radio" name="ret" checked={p.retention_days === 0}
                       onChange={() => setP({ ...p, retention_days: 0 })} /> Don't keep it</label>
              </fieldset>
              <div className={s.actions}>
                <Button type="submit" busy={busy}>Save details</Button>
                {saved && <span className={s.saved} role="status">Saved</span>}
                {error && <span className={s.error} role="alert">{error}</span>}
              </div>
            </form>
          </section>
        )}
        <YourNumbers />
      </div>
    </ConsoleShell>
  );
}
