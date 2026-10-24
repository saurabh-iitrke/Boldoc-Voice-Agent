import type { Delivery } from "../lib/types";
import s from "./Stamp.module.css";

const CHANNEL = { sms: "SMS", web: "browser", email: "email", whatsapp: "WhatsApp" } as const;

/** Violet rubber stamp: shown only for server-confirmed delivery states. */
export function Stamp({ delivery, small = false }: { delivery: Delivery; small?: boolean }) {
  const time = new Date(delivery.at).toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" });
  const failed = delivery.status === "failed";
  const headline = failed ? "Not delivered" : delivery.status === "delivered" ? "Sent" : "Sending";
  return (
    <div className={`${s.stamp} ${small ? s.small : ""} ${failed ? s.failed : ""}`} role="status">
      <span className={s.head}>{headline}</span>
      <span className={s.sub}>
        {CHANNEL[delivery.channel]} to {delivery.recipient_masked}
        {delivery.status === "delivered" ? `, ${time}` : ""}
      </span>
    </div>
  );
}
