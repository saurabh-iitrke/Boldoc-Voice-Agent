import { useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";

import { api } from "./api";
import s from "./admin.module.css";
import { AgentPage } from "./pages/Agent";
import { AuditPage } from "./pages/Audit";
import { CallPage } from "./pages/Call";
import { CallsPage } from "./pages/Calls";
import { LoginPage } from "./pages/Login";
import { OverviewPage } from "./pages/Overview";
import { TestCallPage } from "./pages/TestCall";
import { UsersPage } from "./pages/Users";
import { Shell } from "./Shell";
import { message } from "./ui";

type Auth = { state: "checking" } | { state: "out" } | { state: "in"; username: string }
  | { state: "unreachable"; error: string };

export function App() {
  return (
    <BrowserRouter>
      <Gate />
    </BrowserRouter>
  );
}

/** Signed out, every address shows the sign-in card and stays put, so a link to a call opens that call after it. */
function Gate() {
  const [auth, setAuth] = useState<Auth>({ state: "checking" });
  const [attempt, setAttempt] = useState(0);
  const { pathname } = useLocation();

  useEffect(() => {
    let live = true;
    api.me()
      .then((me) => { if (live) setAuth(me ? { state: "in", username: me.username } : { state: "out" }); })
      .catch((e: unknown) => { if (live) setAuth({ state: "unreachable", error: message(e) }); });
    return () => { live = false; };
  }, [attempt]);

  useEffect(() => {
    const out = () => setAuth({ state: "out" });
    window.addEventListener("admin:signed-out", out);   // any 401: the session ended or was revoked
    return () => window.removeEventListener("admin:signed-out", out);
  }, []);

  useEffect(() => { window.scrollTo(0, 0); }, [pathname]);

  if (auth.state === "checking") return <div className={s.signin} aria-busy="true" />;
  if (auth.state === "unreachable") {
    return (
      <div className={s.signin}>
        <div className={s.signinCard} role="alert">
          <h1 className={s.h1}>Can't reach BolDoc</h1>
          <p className={s.muted}>{auth.error}</p>
          <button type="button" className={s.submit} onClick={() => { setAuth({ state: "checking" }); setAttempt((n) => n + 1); }}>
            Try again
          </button>
        </div>
      </div>
    );
  }
  if (auth.state === "out") return <LoginPage onSignedIn={(username) => setAuth({ state: "in", username })} />;

  const signOut = async () => {
    try { await api.logout(); } catch { /* the cookie is cleared server-side or already gone */ }
    setAuth({ state: "out" });
  };
  return (
    <Shell username={auth.username} onSignOut={signOut}>
      <Routes>
        <Route path="/" element={<OverviewPage />} />
        <Route path="/calls" element={<CallsPage />} />
        <Route path="/calls/:id" element={<CallPage />} />
        <Route path="/test-calls" element={<TestCallPage />} />
        <Route path="/agent" element={<AgentPage />} />
        <Route path="/users" element={<UsersPage />} />
        <Route path="/audit" element={<AuditPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  );
}
