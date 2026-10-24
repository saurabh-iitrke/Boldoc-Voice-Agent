import { DIAL_IN_DISPLAY, DIAL_IN_E164 } from "../../lib/site";

/** QR codes for "tel:<number>" (segno 1.6, version 2, error correction Q), drawn with the standard 4-module quiet
 * zone: the public number, and the line signed-in accounts call (MEMBER_DIAL_IN_E164). Regenerate with segno for a
 * new number; scripts reproduced the first one exactly before the second was added. */
const N = 25;
const CODES: Record<string, string> = {
  "+918035449833":
    "M0 0h7v1h-7zM9 0h2v1h-2zM12 0h5v1h-5zM18 0h7v1h-7zM0 1h1v1h-1zM6 1h1v1h-1zM10 1h1v1h-1zM16 1h1v1h-1zM18 1h1v1h-1zM24 1h1v1h-1zM0 2h1v1h-1zM2 2h3v1h-3zM6 2h1v1h-1zM10 2h1v1h-1zM13 2h2v1h-2zM16 2h1v1h-1zM18 2h1v1h-1zM20 2h3v1h-3zM24 2h1v1h-1zM0 3h1v1h-1zM2 3h3v1h-3zM6 3h1v1h-1zM8 3h2v1h-2zM12 3h1v1h-1zM16 3h1v1h-1zM18 3h1v1h-1zM20 3h3v1h-3zM24 3h1v1h-1zM0 4h1v1h-1zM2 4h3v1h-3zM6 4h1v1h-1zM10 4h3v1h-3zM14 4h1v1h-1zM16 4h1v1h-1zM18 4h1v1h-1zM20 4h3v1h-3zM24 4h1v1h-1zM0 5h1v1h-1zM6 5h1v1h-1zM8 5h1v1h-1zM11 5h1v1h-1zM13 5h2v1h-2zM16 5h1v1h-1zM18 5h1v1h-1zM24 5h1v1h-1zM0 6h7v1h-7zM8 6h1v1h-1zM10 6h1v1h-1zM12 6h1v1h-1zM14 6h1v1h-1zM16 6h1v1h-1zM18 6h7v1h-7zM8 7h4v1h-4zM13 7h1v1h-1zM15 7h1v1h-1zM1 8h2v1h-2zM6 8h1v1h-1zM9 8h1v1h-1zM13 8h1v1h-1zM15 8h1v1h-1zM18 8h2v1h-2zM21 8h1v1h-1zM0 9h2v1h-2zM3 9h1v1h-1zM10 9h1v1h-1zM12 9h1v1h-1zM15 9h1v1h-1zM17 9h2v1h-2zM21 9h2v1h-2zM24 9h1v1h-1zM1 10h4v1h-4zM6 10h4v1h-4zM11 10h12v1h-12zM24 10h1v1h-1zM1 11h1v1h-1zM3 11h3v1h-3zM7 11h1v1h-1zM9 11h1v1h-1zM11 11h1v1h-1zM14 11h1v1h-1zM17 11h2v1h-2zM23 11h1v1h-1zM0 12h4v1h-4zM5 12h5v1h-5zM12 12h3v1h-3zM17 12h2v1h-2zM24 12h1v1h-1zM7 13h2v1h-2zM10 13h3v1h-3zM14 13h2v1h-2zM17 13h2v1h-2zM22 13h1v1h-1zM24 13h1v1h-1zM0 14h2v1h-2zM3 14h4v1h-4zM8 14h5v1h-5zM15 14h1v1h-1zM18 14h4v1h-4zM24 14h1v1h-1zM7 15h1v1h-1zM9 15h2v1h-2zM12 15h2v1h-2zM17 15h1v1h-1zM20 15h1v1h-1zM0 16h3v1h-3zM4 16h4v1h-4zM9 16h1v1h-1zM11 16h1v1h-1zM13 16h1v1h-1zM16 16h6v1h-6zM24 16h1v1h-1zM8 17h4v1h-4zM15 17h2v1h-2zM20 17h1v1h-1zM22 17h3v1h-3zM0 18h7v1h-7zM9 18h2v1h-2zM12 18h1v1h-1zM14 18h1v1h-1zM16 18h1v1h-1zM18 18h1v1h-1zM20 18h1v1h-1zM24 18h1v1h-1zM0 19h1v1h-1zM6 19h1v1h-1zM9 19h1v1h-1zM11 19h1v1h-1zM13 19h2v1h-2zM16 19h1v1h-1zM20 19h1v1h-1zM23 19h1v1h-1zM0 20h1v1h-1zM2 20h3v1h-3zM6 20h1v1h-1zM11 20h1v1h-1zM15 20h6v1h-6zM23 20h2v1h-2zM0 21h1v1h-1zM2 21h3v1h-3zM6 21h1v1h-1zM9 21h2v1h-2zM12 21h1v1h-1zM19 21h2v1h-2zM22 21h2v1h-2zM0 22h1v1h-1zM2 22h3v1h-3zM6 22h1v1h-1zM8 22h2v1h-2zM12 22h3v1h-3zM16 22h1v1h-1zM19 22h2v1h-2zM22 22h3v1h-3zM0 23h1v1h-1zM6 23h1v1h-1zM8 23h1v1h-1zM11 23h1v1h-1zM15 23h3v1h-3zM19 23h1v1h-1zM0 24h7v1h-7zM9 24h3v1h-3zM15 24h6v1h-6zM24 24h1v1h-1z",
  "+918035449820":
    "M0 0h7v1h-7zM13 0h4v1h-4zM18 0h7v1h-7zM0 1h1v1h-1zM6 1h1v1h-1zM8 1h1v1h-1zM12 1h3v1h-3zM18 1h1v1h-1zM24 1h1v1h-1zM0 2h1v1h-1zM2 2h3v1h-3zM6 2h1v1h-1zM9 2h2v1h-2zM13 2h1v1h-1zM15 2h2v1h-2zM18 2h1v1h-1zM20 2h3v1h-3zM24 2h1v1h-1zM0 3h1v1h-1zM2 3h3v1h-3zM6 3h1v1h-1zM8 3h1v1h-1zM11 3h2v1h-2zM14 3h1v1h-1zM18 3h1v1h-1zM20 3h3v1h-3zM24 3h1v1h-1zM0 4h1v1h-1zM2 4h3v1h-3zM6 4h1v1h-1zM8 4h4v1h-4zM13 4h2v1h-2zM18 4h1v1h-1zM20 4h3v1h-3zM24 4h1v1h-1zM0 5h1v1h-1zM6 5h1v1h-1zM10 5h5v1h-5zM16 5h1v1h-1zM18 5h1v1h-1zM24 5h1v1h-1zM0 6h7v1h-7zM8 6h1v1h-1zM10 6h1v1h-1zM12 6h1v1h-1zM14 6h1v1h-1zM16 6h1v1h-1zM18 6h7v1h-7zM8 7h3v1h-3zM12 7h1v1h-1zM14 7h2v1h-2zM1 8h1v1h-1zM3 8h4v1h-4zM8 8h1v1h-1zM10 8h1v1h-1zM13 8h3v1h-3zM17 8h2v1h-2zM20 8h2v1h-2zM23 8h1v1h-1zM2 9h4v1h-4zM8 9h4v1h-4zM14 9h4v1h-4zM20 9h2v1h-2zM4 10h3v1h-3zM10 10h2v1h-2zM14 10h2v1h-2zM17 10h2v1h-2zM20 10h2v1h-2zM24 10h1v1h-1zM2 11h1v1h-1zM7 11h1v1h-1zM14 11h1v1h-1zM16 11h1v1h-1zM22 11h1v1h-1zM24 11h1v1h-1zM0 12h3v1h-3zM4 12h1v1h-1zM6 12h1v1h-1zM10 12h5v1h-5zM17 12h2v1h-2zM24 12h1v1h-1zM0 13h1v1h-1zM3 13h1v1h-1zM5 13h1v1h-1zM8 13h2v1h-2zM11 13h1v1h-1zM13 13h1v1h-1zM15 13h1v1h-1zM17 13h1v1h-1zM19 13h2v1h-2zM22 13h1v1h-1zM0 14h2v1h-2zM4 14h7v1h-7zM13 14h1v1h-1zM15 14h1v1h-1zM17 14h3v1h-3zM21 14h1v1h-1zM23 14h2v1h-2zM0 15h1v1h-1zM4 15h2v1h-2zM10 15h4v1h-4zM16 15h3v1h-3zM22 15h1v1h-1zM24 15h1v1h-1zM0 16h1v1h-1zM2 16h5v1h-5zM8 16h2v1h-2zM11 16h1v1h-1zM16 16h7v1h-7zM24 16h1v1h-1zM8 17h1v1h-1zM10 17h1v1h-1zM14 17h3v1h-3zM20 17h1v1h-1zM0 18h7v1h-7zM11 18h1v1h-1zM14 18h1v1h-1zM16 18h1v1h-1zM18 18h1v1h-1zM20 18h1v1h-1zM24 18h1v1h-1zM0 19h1v1h-1zM6 19h1v1h-1zM8 19h2v1h-2zM12 19h1v1h-1zM14 19h1v1h-1zM16 19h1v1h-1zM20 19h1v1h-1zM23 19h2v1h-2zM0 20h1v1h-1zM2 20h3v1h-3zM6 20h1v1h-1zM8 20h1v1h-1zM10 20h1v1h-1zM12 20h1v1h-1zM15 20h6v1h-6zM24 20h1v1h-1zM0 21h1v1h-1zM2 21h3v1h-3zM6 21h1v1h-1zM8 21h1v1h-1zM10 21h5v1h-5zM16 21h1v1h-1zM18 21h2v1h-2zM23 21h2v1h-2zM0 22h1v1h-1zM2 22h3v1h-3zM6 22h1v1h-1zM11 22h1v1h-1zM20 22h1v1h-1zM23 22h2v1h-2zM0 23h1v1h-1zM6 23h1v1h-1zM8 23h2v1h-2zM11 23h1v1h-1zM13 23h3v1h-3zM18 23h2v1h-2zM22 23h3v1h-3zM0 24h7v1h-7zM12 24h2v1h-2zM15 24h6v1h-6zM24 24h1v1h-1z",
};

/** A page offering a number with no code above shows none. */
export const hasQrFor = (e164: string) => Object.prototype.hasOwnProperty.call(CODES, e164);

export function QrCall({ size = 64, e164 = DIAL_IN_E164, display = DIAL_IN_DISPLAY }: {
  size?: number; e164?: string; display?: string;
}) {
  if (!hasQrFor(e164)) return null;
  const modules = CODES[e164];
  return (
    <svg width={size} height={size} viewBox={`-4 -4 ${N + 8} ${N + 8}`} role="img"
         aria-label={`QR code: scan to call ${display}`}
         style={{ flex: "none", borderRadius: 8, boxShadow: "0 0 0 1px var(--rule)", background: "#fff" }}>
      <rect x="-4" y="-4" width={N + 8} height={N + 8} fill="#fff" />
      <path fill="#121212" d={modules} />
    </svg>
  );
}
