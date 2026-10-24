import { useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";

import { Mark } from "../components/site/Mark";
import s from "./admin.module.css";
import { chooseTheme, savedTheme, type Theme } from "./theme";

const LAST = "/calls/latest";
const NAV: { to: string; label: string; active: (path: string) => boolean }[] = [
  { to: "/", label: "Overview", active: (p) => p === "/" },
  { to: LAST, label: "Last call", active: (p) => p === LAST },
  { to: "/calls", label: "Calls", active: (p) => p.startsWith("/calls") && p !== LAST },
  { to: "/test-calls", label: "Test call", active: (p) => p.startsWith("/test-calls") },
  { to: "/agent", label: "Agent", active: (p) => p.startsWith("/agent") },
  { to: "/users", label: "Users", active: (p) => p.startsWith("/users") },
  { to: "/audit", label: "Audit log", active: (p) => p.startsWith("/audit") },
];

/** The console's shell (same header, same switch), with the admin's pages and who is signed in. */
export function Shell({ username, onSignOut, children }: { username: string; onSignOut: () => void;
  children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(savedTheme);
  const { pathname } = useLocation();
  const dark = theme === "dark";
  const toggle = () => {
    const next: Theme = dark ? "light" : "dark";
    chooseTheme(next);
    setTheme(next);
  };

  return (
    <div className={s.shell}>
      <a href="#main" className={s.skip}>Skip to the page</a>
      <header className={s.header}>
        <Link to="/" className={s.brand} aria-label="BolDoc admin: overview">
          <Mark size={22} /> BolDoc <span className={s.tag}>Admin</span>
        </Link>
        <nav aria-label="Admin" className={s.nav}>
          {NAV.map((n) => {
            const on = n.active(pathname);
            return (
              <Link key={n.to} to={n.to} className={`${s.link} ${on ? s.active : ""}`} aria-current={on ? "page" : undefined}>
                {n.label}
              </Link>
            );
          })}
        </nav>
        <div className={s.who}>
          <span className={s.user} title="Signed in">{username}</span>
          <button type="button" className={s.theme} onClick={toggle}
            aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}>
            {dark ? (
              <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><circle cx="12" cy="12" r="4.2" fill="none" stroke="currentColor" strokeWidth="1.8" /><path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M5.3 18.7l1.6-1.6M17.1 6.9l1.6-1.6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>
            ) : (
              <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" /></svg>
            )}
            <span>{dark ? "Light" : "Dark"}</span>
          </button>
          <button type="button" className={s.signOut} onClick={onSignOut}>Sign out</button>
        </div>
      </header>
      <main id="main" className={s.main} tabIndex={-1}>{children}</main>
    </div>
  );
}
