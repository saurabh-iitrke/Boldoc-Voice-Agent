import { useState } from "react";

import { TALK_LANGUAGES, TALK_DEFAULT } from "../lib/talkLanguage";
import s from "./TalkLanguage.module.css";

const SHOWN = 5; // chips: English, Hinglish and three more; the rest behind "N more"

/** "BolDoc talks in": the language a call from the website starts in (lib/talkLanguage.ts). A select (Call me), or
 * chips (the browser call's start), each language in its own script. */
export function TalkLanguage({ value, onChange, disabled, variant = "select" }: {
  value: string; onChange: (code: string) => void; disabled?: boolean; variant?: "select" | "chips";
}) {
  const picked = TALK_LANGUAGES.findIndex((l) => l.code === value);
  const [all, setAll] = useState(picked >= SHOWN);
  if (variant === "select") {
    return (
      <label className={s.field}>
        <span className={s.label}>BolDoc talks in</span>
        <span className={s.control}>
          <select className={s.select} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
            {TALK_LANGUAGES.map((l) => <option key={l.code} value={l.code}>{l.name}</option>)}
          </select>
        </span>
      </label>
    );
  }
  const shown = all ? TALK_LANGUAGES : TALK_LANGUAGES.slice(0, SHOWN);
  const more = TALK_LANGUAGES.length - SHOWN;
  return (
    <fieldset className={s.chipsField} disabled={disabled}>
      <legend className={s.legend}>BolDoc talks in</legend>
      <div className={s.chips}>
        {shown.map((l) => {
          // English and Hinglish read as such; every other language in its own script
          const own = l.code === TALK_DEFAULT || l.code === "hi-IN";
          return (
            <label key={l.code} className={s.chip} lang={own ? undefined : l.code === "od-IN" ? "or" : l.code.slice(0, 2)}
              title={own ? undefined : l.name}>
              <input type="radio" name="talk-lang" value={l.code} checked={l.code === value}
                onChange={() => onChange(l.code)} />
              <span className={s.dot} aria-hidden="true" />
              {own ? l.name : l.native}
              {!own && <span className="visually-hidden"> ({l.name})</span>}
            </label>
          );
        })}
        {!all && more > 0 && (
          <button type="button" className={s.more} onClick={() => setAll(true)}>{more} more</button>
        )}
      </div>
      <p className={s.hint}>You can switch on the call: just start speaking the other language.</p>
    </fieldset>
  );
}
