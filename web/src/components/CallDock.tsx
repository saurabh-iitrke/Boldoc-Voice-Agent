import { useEffect, useRef, useState } from "react";

import s from "./CallDock.module.css";
import { CloseIcon, KeypadIcon, MicIcon, MicOffIcon, PhoneDownIcon } from "./icons";

const KEYS: [string, string][] = [["1", ""], ["2", "abc"], ["3", "def"], ["4", "ghi"], ["5", "jkl"], ["6", "mno"],
  ["7", "pqrs"], ["8", "tuv"], ["9", "wxyz"], ["*", ""], ["0", "+"], ["#", ""]];

type Props = {
  muted: boolean;
  canControl: boolean;      // live call connected (the recorded call: false for mute and keypad)
  onMute: () => void;
  onDigit: (d: string) => void;
  onEnd: () => void;
  cost?: string;            // "₹4 a minute, ₹16 so far"
  meterLabel?: string;      // whose voice the meter shows (default: the caller's microphone)
};

/** The call's controls, in a bar that stays at the bottom: the caller's microphone level (the --level the page's
 * voice meter writes), Mute, Keypad (tones for menus that ask for them), and End call in the orange of a live line. */
export function CallDock({ muted, canControl, onMute, onDigit, onEnd, cost, meterLabel = "Your microphone" }: Props) {
  const [pad, setPad] = useState(false);
  const [typed, setTyped] = useState("");
  const padRef = useRef<HTMLDivElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const firstKey = useRef<HTMLButtonElement>(null);

  // the keypad takes the focus when it opens and gives it back to its button when it closes
  useEffect(() => { if (pad) firstKey.current?.focus(); }, [pad]);
  const closePad = () => { setPad(false); toggleRef.current?.focus(); };

  useEffect(() => {
    if (!pad) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closePad();
      else if (/^[0-9*#]$/.test(e.key)) press(e.key);
    };
    const onDown = (e: PointerEvent) => { if (!padRef.current?.contains(e.target as Node)) setPad(false); };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("pointerdown", onDown); };
  });

  const press = (d: string) => { onDigit(d); setTyped((t) => (t + d).slice(-14)); };

  return (
    <div className={s.dock} role="toolbar" aria-label="Call controls">
      <div className={`${s.meter} ${muted ? s.meterMuted : ""}`} aria-hidden="true">
        <span className={s.bars}>{[0, 1, 2, 3, 4].map((i) => <span key={i} className={s.bar} />)}</span>
        {muted ? "Microphone muted" : meterLabel}
      </div>

      <div className={s.controls} ref={padRef}>
        <button type="button" className={s.ctl} aria-pressed={muted} disabled={!canControl} onClick={onMute}>
          {muted ? <MicOffIcon size={20} /> : <MicIcon size={20} />}
          <span>{muted ? "Unmute" : "Mute"}</span>
        </button>
        <button ref={toggleRef} type="button" className={s.ctl} aria-expanded={pad} aria-controls="keypad"
          disabled={!canControl} onClick={() => setPad((p) => !p)}>
          <KeypadIcon size={20} />
          <span>Keypad</span>
        </button>
        <button type="button" className={s.end} onClick={onEnd}>
          <PhoneDownIcon size={22} />
          <span>End call</span>
        </button>
        {pad && (
          <div id="keypad" className={s.pad} role="dialog" aria-label="Keypad">
            <div className={s.padHead}>
              <span className={s.padLabel}>Keypad <output className={s.typed} aria-live="polite">{typed}</output></span>
              <button type="button" className={s.close} onClick={closePad} aria-label="Close keypad">
                <CloseIcon size={18} />
              </button>
            </div>
            <div className={s.grid}>
              {KEYS.map(([k, letters], i) => (
                <button key={k} ref={i === 0 ? firstKey : undefined} type="button" className={s.key}
                  onClick={() => press(k)}
                  aria-label={k === "*" ? "Star" : k === "#" ? "Hash" : k}>
                  <span className={s.digit}>{k}</span>
                  <span className={s.letters}>{letters}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className={s.cost}>{cost}</div>
    </div>
  );
}
