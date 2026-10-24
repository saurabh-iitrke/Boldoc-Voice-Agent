import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { track } from "../../lib/beacon";
import { rupees } from "../../lib/format";
import { DIAL_IN_DISPLAY, DIAL_IN_E164, SIGNUP_DOCS, TRIAL } from "../../lib/site";
import { MicIcon } from "../icons";
import { QrCall } from "./QrCall";
import { PHONE_ICON, RING_ICON } from "./icons";
import s from "./StartFree.module.css";

/** "Try it free" (the bar, on a computer): every way to a first call, and what each costs. It used to scroll to the top
 * of the landing, which a visitor was usually looking at already. The free call needs a phone (scan or dial); the other
 * two ways need a free account.
 */
export function StartFree({ className }: { className?: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const wrap = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const title = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (!open) return;
    title.current?.focus();
    const close = (back: boolean) => { setOpen(false); if (back) button.current?.focus(); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close(true); };
    const onDown = (e: PointerEvent) => { if (!wrap.current?.contains(e.target as Node)) close(false); };
    window.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    return () => { window.removeEventListener("keydown", onKey); document.removeEventListener("pointerdown", onDown); };
  }, [open]);

  return (
    <div ref={wrap} className={s.wrap}>
      <button ref={button} type="button" className={className} aria-expanded={open} aria-controls={id}
              onClick={() => { if (!open) track("start_open"); setOpen((v) => !v); }}>
        Try it free
      </button>
      {open && (
        <div id={id} className={s.panel} role="dialog" aria-labelledby={`${id}-t`}>
          <h2 id={`${id}-t`} ref={title} tabIndex={-1} className={s.title}>Start your first document</h2>
          <div className={s.free}>
            <QrCall size={92} />
            <div className={s.freeText}>
              <p className={s.head}>Call from your phone</p>
              <a href={`tel:${DIAL_IN_E164}`} className={s.number} onClick={() => track("cta_tel")}>
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d={PHONE_ICON} /></svg>
                {DIAL_IN_DISPLAY}
              </a>
              <p className={s.note}>
                Scan the code or dial. <strong>Free, no sign-up:</strong> your first {TRIAL.calls} calls, up to{" "}
                {TRIAL.minutes} minutes each.
              </p>
            </div>
          </div>
          <div className={s.account}>
            <p className={s.head}>Or from this computer</p>
            <div className={s.ways}>
              <Link to="/app/call" className={s.way} onClick={() => { track("cta_browser"); setOpen(false); }}>
                <MicIcon size={17} />Talk in browser
              </Link>
              <Link to="/app?call=me" className={s.way} onClick={() => { track("cta_callme"); setOpen(false); }}>
                <svg viewBox="0 0 24 24" aria-hidden="true" className={s.ring}><path d={RING_ICON} /></svg>Call me
              </Link>
            </div>
            <p className={s.note}>
              Sign up with your mobile number: {rupees(TRIAL.signupCredits)} free, enough for about {SIGNUP_DOCS} documents.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
