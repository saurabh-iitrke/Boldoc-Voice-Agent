import { Link } from "react-router-dom";

import { DIAL_IN_DISPLAY, DIAL_IN_E164 } from "../../lib/site";
import { Mark } from "./Mark";
import s from "./SiteFooter.module.css";

export function SiteFooter() {
  return (
    <footer className={s.footer}>
      <div className={s.top}>
        <div className={s.brandCol}>
          <span className={s.brand}><Mark size={22} /> BolDoc</span>
          <p className={s.tag}>Documents by phone call, in your language.</p>
          <a className={s.number} href={`tel:${DIAL_IN_E164}`}>{DIAL_IN_DISPLAY}</a>
        </div>
        <nav aria-label="Product" className={s.col}>
          <h2 className={s.head}>Product</h2>
          <a href="/#how">How it works</a>
          <a href="/#examples">Examples</a>
          <a href="/#languages">Languages</a>
          <a href="/#pricing">Pricing</a>
        </nav>
        <nav aria-label="Help" className={s.col}>
          <h2 className={s.head}>Help</h2>
          <Link to="/help">Help and FAQ</Link>
          <Link to="/sign-in">Sign in</Link>
          <a href="mailto:support@boldoc.in">support@boldoc.in</a>
        </nav>
        <nav aria-label="Legal" className={s.col}>
          <h2 className={s.head}>Legal</h2>
          <Link to="/privacy">Privacy</Link>
          <Link to="/terms">Terms</Link>
          <Link to="/refunds">Refunds</Link>
        </nav>
      </div>
      <div className={s.bottom}>
        <span>Calls, documents and data stay in India.</span>
        <span>Calls are answered by an AI assistant.</span>
      </div>
    </footer>
  );
}
