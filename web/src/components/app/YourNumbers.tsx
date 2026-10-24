import { useEffect, useRef, useState, type FormEvent } from "react";

import a from "../../pages/Account.module.css";
import { API_MODE, ApiError, api } from "../../lib/api";
import { clock, displayMobile } from "../../lib/format";
import type { AccountNumbers } from "../../lib/types";
import { Button } from "../Button";
import { OtpField, PhoneField } from "../Fields";
import s from "./YourNumbers.module.css";

type Step = "idle" | "phone" | "code";

/** Settings → "Your numbers": more phones on one account, each added with a code sent to it. */
export function YourNumbers() {
  const [data, setData] = useState<AccountNumbers | null>(null);
  const [loadError, setLoadError] = useState<string>();
  const [step, setStep] = useState<Step>("idle");
  const [digits, setDigits] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [resendIn, setResendIn] = useState(0);
  const [confirming, setConfirming] = useState<string>();  // the number whose removal is being confirmed
  const [done, setDone] = useState<string>();              // what just changed, said once
  const verifying = useRef(false);
  const phone = `+91${digits}`;

  useEffect(() => {
    api.numbers().then(setData).catch(() => setLoadError("Couldn't load your numbers. Refresh the page to try again."));
  }, []);
  useEffect(() => {
    if (resendIn <= 0) return;
    const t = window.setTimeout(() => setResendIn((n) => n - 1), 1000);
    return () => clearTimeout(t);
  }, [resendIn]);
  // Android Chrome reads the code from the SMS (its last line is "@<our host> #<code>")
  useEffect(() => {
    if (step !== "code" || !("OTPCredential" in window)) return;
    const ac = new AbortController();
    (navigator.credentials as unknown as { get(o: object): Promise<{ code?: string } | null> })
      .get({ otp: { transport: ["sms"] }, signal: ac.signal })
      .then((c) => { if (c?.code) setCode(c.code); })
      .catch(() => undefined);
    return () => ac.abort();
  }, [step]);
  useEffect(() => { if (code.length === 6 && step === "code" && !busy) void add(); }); // a full code submits itself

  function close() {
    setStep("idle"); setDigits(""); setCode(""); setError(undefined); setResendIn(0);
  }

  async function send(e?: FormEvent) {
    e?.preventDefault();
    if (digits.length !== 10) { setError("Enter a 10-digit mobile number."); return; }
    setBusy(true); setError(undefined);
    try {
      const r = await api.sendNumberCode(phone);
      setResendIn(r.resend_after_s); setCode(""); setStep("code");
    } catch (err) {
      if (err instanceof ApiError && err.code === "resend_too_soon") setStep("code");
      setError(err instanceof ApiError ? err.message : "Couldn't send the code. Try again.");
    } finally {
      setBusy(false);
    }
  }

  async function add(e?: FormEvent) {
    e?.preventDefault();
    if (verifying.current) return;
    if (code.length !== 6) { setError("Enter the 6-digit code."); return; }
    verifying.current = true; setBusy(true); setError(undefined);
    try {
      setData(await api.addNumber(phone, code));
      setDone(`Added ${displayMobile(phone)}. Its calls now use this account.`);
      close();
    } catch (err) {
      setCode("");
      setError(err instanceof ApiError ? err.message : "Couldn't add the number. Try again.");
    } finally {
      verifying.current = false; setBusy(false);
    }
  }

  async function remove(e164: string) {
    setBusy(true); setError(undefined);
    try {
      setData(await api.removeNumber(e164));
      setDone(`Removed ${displayMobile(e164)}.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't remove the number. Try again.");
    } finally {
      setBusy(false); setConfirming(undefined);
    }
  }

  const added = data ? data.numbers.length - 1 : 0;
  return (
    <section className={a.panel} aria-labelledby="your-numbers">
      <h2 id="your-numbers" className={a.h2}>Your numbers</h2>
      <p className={a.lede}>
        Call BolDoc from any of these: one account, the same balance and documents. After a phone call, the link comes
        by SMS to the number you called from.
      </p>
      {loadError && <p className={a.error} role="alert">{loadError}</p>}
      {data && (
        <ul className={s.list}>
          {data.numbers.map((n) => (
            <li key={n.phone_e164} className={s.row}>
              <span className={s.number}>+91 {displayMobile(n.phone_e164)}</span>
              {n.primary ? (
                <span className={s.note}>You sign in with this one</span>
              ) : confirming === n.phone_e164 ? (
                <span className={s.confirm}>
                  <span>Its calls will stop using this account.</span>
                  <Button variant="secondary" busy={busy} onClick={() => void remove(n.phone_e164)}>Remove</Button>
                  <Button variant="quiet" onClick={() => setConfirming(undefined)}>Keep</Button>
                </span>
              ) : (
                <Button variant="quiet" onClick={() => { setConfirming(n.phone_e164); setDone(undefined); }}>
                  Remove
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {data && step === "idle" && (added >= data.max_added ? (
        <p className={a.muted}>That's the most an account can have: {data.max_added} besides the one you sign in with.</p>
      ) : (
        <div>
          <Button variant="secondary" onClick={() => { setStep("phone"); setDone(undefined); setError(undefined); }}>
            Add a number
          </Button>
        </div>
      ))}
      {step === "phone" && (
        <form className={s.add} onSubmit={send} noValidate>
          <PhoneField value={digits} onChange={(d) => { setDigits(d); setError(undefined); }} error={error} autoFocus />
          <p className={s.hint}>We'll text a code to that phone. Documents it made before show here too.</p>
          <div className={s.buttons}>
            <Button type="submit" busy={busy}>Send code</Button>
            <Button variant="quiet" onClick={close}>Cancel</Button>
          </div>
        </form>
      )}
      {step === "code" && (
        <form className={s.add} onSubmit={add} noValidate>
          <p className={s.hint}>
            Sent to <strong>+91 {displayMobile(phone)}</strong>. It can take a minute and may come from an unfamiliar
            sender.{" "}
            <button type="button" className={s.link} onClick={() => { setStep("phone"); setError(undefined); }}>
              Change number
            </button>
          </p>
          <OtpField value={code} onChange={(c) => { setCode(c); setError(undefined); }} error={error} autoFocus />
          <div className={s.buttons}>
            <Button type="submit" busy={busy}>Add this number</Button>
            {resendIn > 0
              ? <span className={s.hint}>Resend the code in {clock(resendIn)}</span>
              : <Button variant="quiet" onClick={() => void send()}>Resend code</Button>}
            <Button variant="quiet" onClick={close}>Cancel</Button>
          </div>
          {API_MODE === "mock" && <p className={s.dev}>Preview mode: the code is 246810.</p>}
        </form>
      )}
      {step === "idle" && error && <p className={a.error} role="alert">{error}</p>}
      {done && <p className={a.saved} role="status">{done}</p>}
    </section>
  );
}
