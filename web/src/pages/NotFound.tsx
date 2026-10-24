import { useEffect } from "react";
import { Link } from "react-router-dom";

import { SiteFooter } from "../components/site/SiteFooter";
import { SiteHeader } from "../components/site/SiteHeader";
import { DIAL_IN_DISPLAY, DIAL_IN_E164 } from "../lib/site";
import s from "../styles/chrome.module.css";

export function NotFound() {
  useEffect(() => { document.title = "Page not found · BolDoc"; }, []);
  return (
    <>
      <SiteHeader />
      <main className={s.focus}>
        <section className={s.focusCard} aria-labelledby="nf-h">
          <h1 id="nf-h" className={s.focusTitle}>This page doesn't exist</h1>
          <p className={s.focusLead}>
            The address may be mistyped, or the page may have moved. Everything at BolDoc starts with a call.
          </p>
          <div className={s.focusActions}>
            <Link className={`${s.btn} ${s.btnInk}`} to="/">Home page</Link>
            <a className={`${s.btn} ${s.btnLine}`} href={`tel:${DIAL_IN_E164}`}>Call {DIAL_IN_DISPLAY}</a>
          </div>
        </section>
      </main>
      <SiteFooter />
    </>
  );
}
