import { useEffect, useRef, useState } from "react";

import { ApiError, api } from "../../lib/api";
import { displayMobile } from "../../lib/format";
import { useDialIn } from "../../lib/dialIn";
import { useTalkLanguage } from "../../lib/talkLanguage";
import type { AccountNumber } from "../../lib/types";
import { Button } from "../Button";
import { TalkLanguage } from "../TalkLanguage";
import s from "./CallMe.module.css";

type Step =
  | { kind: "closed" }
  | { kind: "choose" }
  | { kind: "calling"; id: string; number: string; until: number }
  | { kind: "answered"; id: string; number: string }
  | { kind: "ended" }
  | { kind: "missed"; message: string };

const ENDED_SHOWN_MS = 5000;  // "Call ended" stays this long, then the panel closes itself

/** "Call me": BolDoc rings one of the account's own numbers, and the assistant answers when it's picked up. The page
 * opens it (Home's "Call me" card); it closes on Cancel or Close, and by itself a few seconds after the call ends.
 * reveal: they came from the landing's "Call me" (/app?call=me, through sign-in), so it scrolls into view as it opens.
 */
export function CallMe({ open, onClose, reveal = false }: { open: boolean; onClose: () => void; reveal?: boolean }) {
  const [step, setStep] = useState<Step>({ kind: "closed" });
  const [numbers, setNumbers] = useState<AccountNumber[]>([]);
  const [chosen, setChosen] = useState<string>();
  const [lang, setLang, picked] = useTalkLanguage();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const polling = useRef<number | undefined>(undefined);
  const panel = useRef<HTMLDivElement>(null);
  const line = useDialIn();

  useEffect(() => () => window.clearTimeout(polling.current), []);
  useEffect(() => {
    if (open) void start();
    else {
      window.clearTimeout(polling.current);
      setStep({ kind: "closed" });
    }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps -- follows the page's open/closed
  useEffect(() => {
    if (step.kind === "choose") panel.current?.scrollIntoView({ block: "nearest", behavior: reveal ? "auto" : "smooth" });
  }, [step.kind, reveal]);

  async function start() {
    setError(undefined);
    setStep({ kind: "choose" });
    try {
      const n = (await api.numbers()).numbers;
      setNumbers(n);
      setChosen((c) => c ?? n[0]?.phone_e164);
    } catch {
      setError("Couldn't load your numbers. Refresh the page to try again.");
    }
  }

  async function ring() {
    if (!chosen) return;
    setBusy(true);
    setError(undefined);
    try {
      const r = await api.callMe(chosen, picked);  // none picked: their last call's language
      const calling = { kind: "calling" as const, id: r.id, number: chosen, until: Date.now() + (r.ring_s + 15) * 1000 };
      setStep(calling);
      watch(calling);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "We couldn't place the call. Try again, or call us.");
    } finally {
      setBusy(false);
    }
  }

  function watch(call: Extract<Step, { kind: "calling" }>) {
    polling.current = window.setTimeout(async () => {
      try {
        const { status } = await api.callMeStatus(call.id);
        if (status === "answered") {
          setStep({ kind: "answered", id: call.id, number: call.number });
          return follow(call.id);
        }
        if (status === "failed") return setStep({ kind: "missed", message: "The call couldn't be placed." });
        if (status === "refused") {
          return setStep({ kind: "missed", message: "BolDoc couldn't take the call just then." });
        }
      } catch { /* a missed poll: keep watching until the ring time is over */ }
      if (Date.now() > call.until) {
        return setStep({ kind: "missed", message: "No answer." });
      }
      watch(call);
    }, 2000);
  }

  /** On the call: watch for its end, then say so and close. A status gone (it outlives no call) is an end too. */
  function follow(id: string) {
    polling.current = window.setTimeout(async () => {
      try {
        const { status } = await api.callMeStatus(id);
        if (status !== "ended") return follow(id);
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 404)) return follow(id);  // a missed poll: keep watching
      }
      setStep({ kind: "ended" });
      polling.current = window.setTimeout(onClose, ENDED_SHOWN_MS);
    }, 3000);
  }

  if (step.kind === "closed") return null;
  return (
    <div ref={panel} id="call-me" className={s.panel} role="group" aria-labelledby="call-me-h">
      <h3 id="call-me-h" className={s.h3}>Call me</h3>
      {step.kind === "choose" && (
        <>
          <p className={s.text}>
            BolDoc rings your phone from {line.display}. Pick up and say what you need written; the link comes by
            SMS and the document shows here too.
          </p>
          {numbers.length > 1 ? (
            <fieldset className={s.numbers}>
              <legend className={s.legend}>Call this number</legend>
              {numbers.map((n) => (
                <label key={n.phone_e164} className={s.number}>
                  <input type="radio" name="call-me-number" value={n.phone_e164} checked={chosen === n.phone_e164}
                    onChange={() => setChosen(n.phone_e164)} />
                  +91 {displayMobile(n.phone_e164)}
                </label>
              ))}
            </fieldset>
          ) : chosen ? <p className={s.only}>+91 {displayMobile(chosen)}</p> : null}
          <TalkLanguage value={lang} onChange={setLang} disabled={busy} />
          <div className={s.actions}>
            <Button busy={busy} disabled={!chosen} onClick={() => void ring()}>Call me now</Button>
            <Button variant="quiet" onClick={onClose}>Cancel</Button>
          </div>
          <p className={s.fine}>BolDoc only calls numbers on your account. Add another in My details.</p>
        </>
      )}
      {step.kind === "calling" && (
        <p className={s.status} role="status">
          <span className={s.pulse} aria-hidden="true" />
          Ringing +91 {displayMobile(step.number)}. Pick up to talk to BolDoc.
        </p>
      )}
      {step.kind === "answered" && (
        <>
          <p className={s.status} role="status">
            Connected. Talk to BolDoc on your phone; your document will appear here as well.
          </p>
          <div className={s.actions}>
            <Button variant="quiet" onClick={onClose}>Close</Button>
          </div>
        </>
      )}
      {step.kind === "ended" && (
        <>
          <p className={s.status} role="status">The call has ended. Your documents are below.</p>
          <div className={s.actions}>
            <Button variant="quiet" onClick={onClose}>Close</Button>
          </div>
        </>
      )}
      {step.kind === "missed" && (
        <>
          <p className={s.text} role="status">{step.message} Try again, or call {line.display} yourself.</p>
          <div className={s.actions}>
            <Button onClick={() => void ring()} busy={busy}>Try again</Button>
            <Button variant="quiet" onClick={onClose}>Close</Button>
          </div>
        </>
      )}
      {error && <p className={s.error} role="alert">{error}</p>}
    </div>
  );
}
