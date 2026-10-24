import { useState, type ReactNode } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "../../lib/auth";
import { chooseTheme, isConsolePath, savedTheme, type Theme } from "../../lib/theme";
import { Mark } from "../site/Mark";
import s from "./ConsoleShell.module.css";

const NAV = [
  { to: "/app", label: "Home", end: true },
  { to: "/app/balance", label: "Balance", end: false },
  { to: "/app/settings", label: "My details", end: false },
];

/** The brand on a signed-in page: the public site, in a new tab, so the console (or a call) stays open. */
export function ConsoleBrand({ className, size = 22 }: { className?: string; size?: number }) {
  return (
    <a href="/" target="_blank" rel="noopener noreferrer" className={className}
       aria-label="BolDoc home page (opens in a new tab)">
      <Mark size={size} /> BolDoc
    </a>
  );
}

/** The signed-in area: one header, the user's number, a light/dark switch (light by default), sign out. A document
 * opens inside it too: Home (where the documents are listed) stays lit, and the switch is left out, because the
 * document is paper and always light (lib/theme.ts). */
export function ConsoleShell({ children }: { children: ReactNode }) {
  const { me, signOut } = useAuth();
  const nav = useNavigate();
  const { pathname } = useLocation();
  const onDocument = pathname.startsWith("/app/documents/");
  const [theme, setTheme] = useState(savedTheme);
  const dark = theme === "dark";
  const toggle = () => {
    const next: Theme = dark ? "light" : "dark";
    chooseTheme(next);
    setTheme(next);
  };

  return (
    <div className={s.shell}>
      <header className={s.header}>
        <ConsoleBrand className={s.brand} />
        <nav aria-label="Your account" className={s.nav}>
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}
              className={({ isActive }) => `${s.link} ${isActive || (onDocument && n.to === "/app") ? s.active : ""}`}>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className={s.end}>
          <span className={s.number}>{me?.phone_masked}</span>
          {isConsolePath(pathname) && (
            <button type="button" className={s.theme} onClick={toggle}
              aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}>
              {dark ? (
                <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><circle cx="12" cy="12" r="4.2" fill="none" stroke="currentColor" strokeWidth="1.8" /><path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M5.3 18.7l1.6-1.6M17.1 6.9l1.6-1.6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>
              ) : (
                <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" /></svg>
              )}
              <span>{dark ? "Light" : "Dark"}</span>
            </button>
          )}
          <button type="button" className={s.signOut} onClick={async () => { await signOut(); nav("/", { replace: true }); }}>
            Sign out
          </button>
        </div>
      </header>
      <main className={s.main}>{children}</main>
      <footer className={s.foot}>
        <span>Calls are answered by an AI assistant.</span>
        <nav aria-label="Help and legal" className={s.footLinks}>
          <Link to="/help">Help</Link>
          <Link to="/privacy">Privacy</Link>
          <Link to="/terms">Terms</Link>
          <a href="mailto:support@boldoc.in">support@boldoc.in</a>
        </nav>
      </footer>
    </div>
  );
}
