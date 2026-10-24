import { useEffect, useRef, useState, type FormEvent } from "react";

import { Mark } from "../../components/site/Mark";
import { api, ApiError, MOCK, type SignInMethod } from "../api";
import s from "../admin.module.css";
import { message, useTitle } from "../ui";

/** Username and password, and optionally a 6-digit code: from the authenticator app, or sent by SMS to the admin's own
   mobile once the password is right.
*/
export function LoginPage({ onSignedIn }: { onSignedIn: (username: string) => void }) {
  useTitle("Sign in");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [method, setMethod] = useState<SignInMethod>("totp");
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [resendIn, setResendIn] = useState(0);
  const [busy, setBusy] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const codeRef = useRef<HTMLInputElement>(null);
  const digits = code.replace(/\D/g, "");
  const ready = username.trim() !== "" && password !== "" && (digits.length === 0 || digits.length === 6);

  useEffect(() => {  // the wait before another SMS code may be sent
    if (resendIn <= 0) return;
    const t = window.setTimeout(() => setResendIn((n) => n - 1), 1000);
    return () => window.clearTimeout(t);
  }, [resendIn]);

  const sendSms = async () => {
    if (sending) return;
    if (username.trim() === "" || password === "") {
      setError("Enter your username and password first: the code goes to your mobile once they're right.");
      return;
    }
    setSending(true);
    setError(null);
    try {
      const sent = await api.sendSmsCode(username.trim().toLowerCase(), password);
      setMethod("sms");
      setSentTo(sent.sent_to);
      setResendIn(sent.resend_after_s);
      setCode("");
      codeRef.current?.focus();
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? "That username or password didn't match." : message(err));
    } finally {
      setSending(false);
    }
  };

  const useAuthenticator = () => {
    setMethod("totp");
    setCode("");
    setError(null);
    codeRef.current?.focus();
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    try {
      const me = await api.login(username.trim().toLowerCase(), password, digits, method);
      onSignedIn(me.username);
    } catch (err) {
      setError(err instanceof ApiError && err.status === 429 ? "Too many tries. Wait a few minutes, then try again."
        : err instanceof ApiError && err.status === 401 && (method === "sms" || err.code === "code_needed")
          ? message(err)
          : err instanceof ApiError && err.status === 401 && digits === ""
            ? "That username or password didn't match."
            : err instanceof ApiError && err.status === 401
              ? "That username, password or code didn't match. A code works once, within 30 seconds."
              : message(err));
      setCode("");
      setBusy(false);
      codeRef.current?.focus();
    }
  };

  return (
    <div className={s.signin}>
      <main className={s.signinCard}>
        <p className={s.brand}><Mark size={22} /> BolDoc <span className={s.tag}>admin</span></p>
        <div>
          <h1 className={s.h1}>Sign in</h1>
          <p className={s.lede}>For the people who run BolDoc. Every sign-in, revealed number and opened transcript is
            logged.</p>
        </div>
        <form className={s.form} onSubmit={submit} noValidate>
          <label className={s.field}>
            <span>Username</span>
            <input className={s.input} name="username" autoComplete="username" autoCapitalize="none" spellCheck={false}
              value={username} onChange={(e) => setUsername(e.target.value)} required autoFocus />
          </label>
          <label className={s.field}>
            <span>Password</span>
            <input className={s.input} name="password" type="password" autoComplete="current-password" value={password}
              onChange={(e) => setPassword(e.target.value)} required />
          </label>
          <div className={s.codeField}>
            <label className={s.field}>
              <span>{method === "sms" ? `Code sent by SMS to ${sentTo ?? "your mobile"}`
                : "Code from your authenticator app (optional)"}</span>
              <input ref={codeRef} className={`${s.input} ${s.code}`} name="code" inputMode="numeric"
                autoComplete="one-time-code" pattern="[0-9 ]*" maxLength={7} placeholder="123 456" value={code}
                onChange={(e) => setCode(e.target.value)}
                aria-describedby={error ? "login-error code-ways" : "code-ways"} />
            </label>
            <p id="code-ways" className={s.codeWays}>
              {method === "totp" ? (
                <button type="button" className={s.linkBtn} onClick={sendSms} disabled={sending}>
                  {sending ? "Sending a code…" : "Get a code by SMS instead"}
                </button>
              ) : (
                <>
                  <button type="button" className={s.linkBtn} onClick={sendSms} disabled={sending || resendIn > 0}>
                    {sending ? "Sending…" : resendIn > 0 ? `Send again in ${resendIn} s` : "Send again"}
                  </button>
                  <button type="button" className={s.linkBtn} onClick={useAuthenticator}>
                    Use the authenticator app
                  </button>
                </>
              )}
            </p>
          </div>
          {error && <p id="login-error" className={s.error} role="alert">{error}</p>}
          <button type="submit" className={s.submit} disabled={busy || !ready}>{busy ? "Signing in…" : "Sign in"}</button>
        </form>
        <p className={s.footnote}>
          {MOCK ? "Mock data: any username, a password of 12 or more characters; a code is optional (246810)."
            : "The code is optional for now: your username and password are enough."}
        </p>
      </main>
    </div>
  );
}
