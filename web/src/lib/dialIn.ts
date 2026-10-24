import { useAuth } from "./auth";
import { DIAL_IN_E164 } from "./site";

/** Signed in, an account calls its own line (/api/me's dial_in); the public pages show the trial number (lib/site). */
export function useDialIn(): { e164: string; display: string } {
  const { me } = useAuth();
  const e164 = me?.dial_in || DIAL_IN_E164;
  return { e164, display: displayDialIn(e164) };
}

// two-digit STD codes (Delhi, Pune, Mumbai, Kolkata, Hyderabad, Chennai, Ahmedabad, Bengaluru)
const METRO = /^(11|20|22|33|40|44|79|80)/;

/** "+918035449833" -> "+91 80 3544 9833"; other landlines -> "+91 124 456 7890"; a mobile -> "+91 98765 43210".
 * BolDoc's numbers are landlines (DIDs), so a metro code wins over a mobile-looking "80…". */
export function displayDialIn(e164: string): string {
  const d = /^\+91(\d{10})$/.exec(e164)?.[1];
  if (!d) return e164;
  if (METRO.test(d)) return `+91 ${d.slice(0, 2)} ${d.slice(2, 6)} ${d.slice(6)}`;
  if (/^[6-9]/.test(d)) return `+91 ${d.slice(0, 5)} ${d.slice(5)}`;
  return `+91 ${d.slice(0, 3)} ${d.slice(3, 6)} ${d.slice(6)}`;
}
