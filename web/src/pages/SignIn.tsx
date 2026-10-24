import { useEffect, useRef, useState, type FormEvent } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";

import { Button } from "../components/Button";
import { OtpField, PhoneField } from "../components/Fields";
import { SiteFooter } from "../components/site/SiteFooter";
import { SiteHeader } from "../components/site/SiteHeader";
import { API_MODE, ApiError, api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { track } from "../lib/beacon";
import { clock, displayMobile, rupees } from "../lib/format";
import { DIAL_IN_DISPLAY, DIAL_IN_E164, TRIAL } from "../lib/site";
import type { VerifyByCall } from "../lib/types";
import s from "./SignIn.module.css";

type Step = "phone" | "code" | "call";

export function SignIn() {
  const { me, setMe } = useAuth();
  const nav = useNavigate();
  const [params] = useSearchParams();
  // back to the page that sent them here: a path on this site ("/app?call=me"), never another site
  const asked = params.get("next") ?? "";
  const next = /^\/(?![/\\])/.test(asked) ? asked : "/app";
  const [digits, setDigits] = useState("");
  const [code, setCode] = useState("");
  const [step, setStep] = useState<Step>("phone");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [resendIn, setResendIn] = useState(0);
  const [byCall, setByCall] = useState<VerifyByCall & { left: number }>();
  const verifying = useRef(false);

  useEffect(() => { document.title = "Sign in · BolDoc"; }, []);
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

  useEffect(() => { if (code.length === 6 && step === "code" && !busy) void verify(); }); // auto-submit full code

  // "verify by calling": poll until the call from this number arrives (or the window closes)
  useEffect(() => {
    if (step !== "call" || !byCall) return;
    if (byCall.left <= 0) {
      setError("We didn't get a call from that number. Try again, or get a code by SMS.");
      return;
    }
    const t = window.setTimeout(async () => {
      try {
        const r = await api.verifyByCallStatus(byCall.nonce);
        if (r.status === "verified") { done(r.me); return; }
      } catch (err) {
        if (err instanceof ApiError && err.status === 410) { setByCall({ ...byCall, left: 0 }); return; }
      }
      setByCall({ ...byCall, left: byCall.left - 2 });
    }, 2000);
    return () => clearTimeout(t);
  }, [step, byCall]); // eslint-disable-line react-hooks/exhaustive-deps

  if (me) return <Navigate to={next} replace />;

  const phone = `+91${digits}`;
  const validPhone = () => {
    if (/^[6-9]\d{9}$/.test(digits)) return true;
    setError("Enter a 10-digit mobile number that starts with 6, 7, 8 or 9.");
    return false;
  };

  function done(signedIn: Parameters<typeof setMe>[0]) {
    track("signin_done");
    setMe(signedIn);
    nav(next, { replace: true });
  }

  async function send(e?: FormEvent) {
    e?.preventDefault();
    if (!validPhone()) return;
    setBusy(true);
    setError(undefined);
    track("signin_start");
    try {
      const r = await api.requestOtp(phone);
      setStep("code");
      setCode("");
      setResendIn(r.resend_after_s);
    } catch (err) {
      if (err instanceof ApiError && err.code === "resend_too_soon") { setStep("code"); setError(err.message); }
      else setError(err instanceof ApiError ? err.message : "We couldn't send the code. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  }

  async function verify(e?: FormEvent) {
    e?.preventDefault();
    if (verifying.current) return;
    if (code.length !== 6) { setError("Enter all 6 digits of the code."); return; }
    verifying.current = true;
    setBusy(true);
    setError(undefined);
    try {
      done(await api.verifyOtp(phone, code));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "We couldn't check the code. Try again.");
      setCode("");
    } finally {
      setBusy(false);
      verifying.current = false;
    }
  }

  async function callInstead() {
    if (!validPhone()) return;
    setBusy(true);
    setError(undefined);
    try {
      const r = await api.verifyByCall(phone);
      setByCall({ ...r, left: r.expires_in_s });
      setStep("call");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <SiteHeader />
      <main className={s.screen}>
        <div className={s.card}>
          {step === "phone" && (
            <form onSubmit={send} className={s.form} noValidate>
              <div className={s.intro}>
                <h1 className={s.h1}>Sign in or sign up</h1>
                {next.startsWith("/app/call") && <p className={s.why}>Sign in to talk to BolDoc in your browser.</p>}
                {next.startsWith("/app?call=me") && <p className={s.why}>Sign in, and BolDoc will call your phone.</p>}
                <p className={s.lead}>
                  Use your mobile number. New here? You get {rupees(TRIAL.signupCredits)} free, and every document
                  you made on a call from this number is already yours.
                </p>
              </div>
              <PhoneField value={digits} onChange={(d) => { setDigits(d); setError(undefined); }} error={error} autoFocus />
              <Button type="submit" size="lg" busy={busy}>Send code</Button>
            </form>
          )}
          {step === "code" && (
            <form onSubmit={verify} className={s.form} noValidate>
              <div className={s.intro}>
                <h1 className={s.h1}>Enter the code</h1>
                <p className={s.lead}>
                  Sent to <strong>{displayMobile(phone)}</strong>. It can take a minute and may come from an
                  unfamiliar sender.{" "}
                  <button type="button" className={s.link} onClick={() => { setStep("phone"); setError(undefined); }}>
                    Change number
                  </button>
                </p>
              </div>
              <OtpField value={code} onChange={(c) => { setCode(c); setError(undefined); }} error={error} autoFocus />
              <Button type="submit" size="lg" busy={busy}>Verify and continue</Button>
              <div className={s.alt}>
                {resendIn > 0 ? <span>Resend the code in {clock(resendIn)}</span> : (
                  <button type="button" className={s.link} onClick={() => void send()}>Resend code</button>
                )}
                <button type="button" className={s.link} onClick={() => void callInstead()}>
                  No SMS? Verify by calling
                </button>
              </div>
              {API_MODE === "mock" && <p className={s.devNote}>Preview mode: the code is 246810.</p>}
            </form>
          )}
          {step === "call" && byCall && (
            <div className={s.form}>
              <div className={s.intro}>
                <h1 className={s.h1}>Call us to verify</h1>
                <p className={s.lead}>
                  From <strong>{displayMobile(phone)}</strong>, call the number below. BolDoc answers, confirms your
                  number and hangs up; this page signs you in.
                </p>
              </div>
              <a className={s.callNumber} href={`tel:${DIAL_IN_E164}`}>{DIAL_IN_DISPLAY}</a>
              {byCall.left > 0
                ? <p className={s.waiting} aria-live="polite"><span className={s.dot} aria-hidden="true" />
                    Waiting for your call · {clock(byCall.left)}</p>
                : <p className={s.error} role="alert">{error}</p>}
              <div className={s.alt}>
                <button type="button" className={s.link} onClick={() => { setStep("code"); setError(undefined); }}>
                  Back to the SMS code
                </button>
                {byCall.left <= 0 && (
                  <button type="button" className={s.link} onClick={() => void callInstead()}>Try again</button>
                )}
              </div>
            </div>
          )}
        </div>
        <p className={s.fine}>
          Calls are answered by an AI assistant. Audio isn't recorded; the conversation's text is kept for 30 days.
        </p>
      </main>
      <SiteFooter />
    </>
  );
}
