import { useId } from "react";

import s from "./Fields.module.css";

export function PhoneField({ value, onChange, error, autoFocus }: {
  value: string; onChange: (digits: string) => void; error?: string; autoFocus?: boolean;
}) {
  const id = useId();
  const shown = value.length > 5 ? `${value.slice(0, 5)} ${value.slice(5)}` : value;
  return (
    <div className={s.field}>
      <label htmlFor={id} className={s.label}>Mobile number</label>
      <div className={`${s.box} ${error ? s.invalid : ""}`}>
        <span className={s.prefix} aria-hidden="true">+91</span>
        <input id={id} className={s.input} inputMode="tel" autoComplete="tel-national" placeholder="98765 43210"
          value={shown} autoFocus={autoFocus} aria-invalid={!!error} aria-describedby={error ? `${id}-err` : undefined}
          onChange={(e) => onChange(e.target.value.replace(/\D/g, "").replace(/^91(?=\d{10}$)/, "").slice(0, 10))} />
      </div>
      {error && <p id={`${id}-err`} className={s.error} role="alert">{error}</p>}
    </div>
  );
}

export function OtpField({ value, onChange, error, autoFocus }: {
  value: string; onChange: (code: string) => void; error?: string; autoFocus?: boolean;
}) {
  const id = useId();
  return (
    <div className={s.field}>
      <label htmlFor={id} className={s.label}>6-digit code</label>
      <div className={`${s.otp} ${error ? s.invalid : ""}`}>
        {Array.from({ length: 6 }, (_, i) => (
          <span key={i} className={`${s.slot} ${i === Math.min(value.length, 5) ? s.active : ""}`} aria-hidden="true">
            {value[i] ?? ""}
          </span>
        ))}
        <input id={id} className={s.otpInput} inputMode="numeric" autoComplete="one-time-code" maxLength={6}
          value={value} autoFocus={autoFocus} aria-invalid={!!error} aria-describedby={error ? `${id}-err` : undefined}
          onChange={(e) => onChange(e.target.value.replace(/\D/g, "").slice(0, 6))} />
      </div>
      {error && <p id={`${id}-err`} className={s.error} role="alert">{error}</p>}
    </div>
  );
}
