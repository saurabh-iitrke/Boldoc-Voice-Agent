import { useEffect, useId, useState } from "react";
import { Link } from "react-router-dom";

import { track } from "../../lib/beacon";
import { DIAL_IN_DISPLAY, DIAL_IN_E164 } from "../../lib/site";
import { PHONE_ICON } from "./icons";
import { Mark } from "./Mark";
import s from "./SiteHeader.module.css";
import { StartFree } from "./StartFree";

export { PHONE_ICON };

const NAV = [
  { href: "/#how", label: "How it works" },
  { href: "/#examples", label: "Examples" },
  { href: "/#pricing", label: "Pricing" },
  { href: "/help", label: "Help" },
];

export function SiteHeader({ signedIn = false }: { signedIn?: boolean }) {
  const [open, setOpen] = useState(false);
  const menuId = useId();
  useEffect(() => {
    if (!open) return;
    const close = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [open]);

  return (
    <header className={s.header}>
      <Link to="/" className={s.brand} aria-label="BolDoc home">
        <Mark size={23} />
        BolDoc
      </Link>
      <nav aria-label="Primary" className={s.nav}>
        {NAV.map((n) => (
          <a key={n.href} href={n.href} className={s.navLink}>{n.label}</a>
        ))}
      </nav>
      <div className={s.end}>
        {signedIn
          ? <Link to="/app" className={s.navLink}>Your documents</Link>
          : <Link to="/sign-in" className={`${s.navLink} ${s.signIn}`}>Sign in</Link>}
        <div className={s.ctaDesktop}><StartFree className={s.cta} /></div>
        {/* on a phone the free call is one tap away */}
        <a href={`tel:${DIAL_IN_E164}`} className={`${s.cta} ${s.ctaPhone}`} onClick={() => track("cta_tel")}
           aria-label={`Call BolDoc free on ${DIAL_IN_DISPLAY}`}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d={PHONE_ICON} /></svg>
          Call free
        </a>
        <button type="button" className={s.menuButton} aria-expanded={open} aria-controls={menuId}
                onClick={() => setOpen((v) => !v)}>
          <span className="visually-hidden">{open ? "Close menu" : "Open menu"}</span>
          <span className={s.burger} data-open={open} aria-hidden="true" />
        </button>
      </div>
      <div id={menuId} className={s.menu} data-open={open} hidden={!open}>
        {NAV.map((n) => (
          <a key={n.href} href={n.href} onClick={() => setOpen(false)}>{n.label}</a>
        ))}
        <Link to="/app/call">Talk in browser</Link>
        <Link to="/app?call=me">Get a call from BolDoc</Link>
        {signedIn ? <Link to="/app">Your documents</Link> : <Link to="/sign-in">Sign in</Link>}
      </div>
    </header>
  );
}
