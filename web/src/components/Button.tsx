import type { ButtonHTMLAttributes, ReactNode } from "react";

import s from "./Button.module.css";

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "quiet";
  size?: "md" | "lg";
  icon?: ReactNode;
  busy?: boolean;
};

export function Button({ variant = "primary", size = "md", icon, busy = false, children, className, disabled, ...rest }: Props) {
  return (
    <button type="button" {...rest} disabled={disabled || busy} aria-busy={busy || undefined}
      className={`${s.btn} ${s[variant]} ${s[size]} ${className ?? ""}`}>
      {busy ? <span className={s.spinner} aria-hidden="true" /> : icon}
      <span>{children}</span>
    </button>
  );
}
