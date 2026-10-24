// Stroke icons drawn for this app (1.75 stroke, round joins) so they share one visual weight.
type P = { size?: number; title?: string };
const base = (size: number) => ({ width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
  strokeWidth: 1.75, strokeLinecap: "round" as const, strokeLinejoin: "round" as const });

export const MicIcon = ({ size = 22 }: P) => (
  <svg {...base(size)} aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" /></svg>
);
export const MicOffIcon = ({ size = 22 }: P) => (
  <svg {...base(size)} aria-hidden="true"><path d="M15 9.3V6a3 3 0 0 0-5.7-1.3M9 9v2a3 3 0 0 0 4.7 2.5M5.5 11a6.5 6.5 0 0 0 10.8 4.9M18.5 11a6.4 6.4 0 0 1-.4 2.2M12 17.5V21M4 4l16 16" /></svg>
);
export const KeypadIcon = ({ size = 22 }: P) => (
  <svg {...base(size)} aria-hidden="true">{[6, 12, 18].flatMap((y) => [6, 12, 18].map((x) => <circle key={`${x}${y}`} cx={x} cy={y - 2} r="1.3" />))}<circle cx="12" cy="21" r="1.3" /></svg>
);
export const PhoneDownIcon = ({ size = 26 }: P) => (
  <svg {...base(size)} aria-hidden="true"><path d="M3.5 14.5c4.7-4.2 12.3-4.2 17 0l-1.8 2.6a1.2 1.2 0 0 1-1.5.4l-2.6-1.2a1.2 1.2 0 0 1-.7-1.1v-1.6a11 11 0 0 0-3.8 0v1.6a1.2 1.2 0 0 1-.7 1.1l-2.6 1.2a1.2 1.2 0 0 1-1.5-.4z" /></svg>
);
export const PhoneIcon = ({ size = 20 }: P) => (
  <svg {...base(size)} aria-hidden="true"><path d="M5 4h3l1.6 4.2-2 1.3a11 11 0 0 0 5 5l1.3-2L18 14v3a2 2 0 0 1-2.2 2A15 15 0 0 1 3 6.2 2 2 0 0 1 5 4z" /></svg>
);
/** The three ways to start a document (console Home): a handset, the same ringing in, and the browser's MicIcon. */
export const HandsetIcon = ({ size = 22 }: P) => (
  <svg {...base(size)} aria-hidden="true"><path d="M5.2 3.5h3.1l1.9 4.8-2.4 1.5a11.5 11.5 0 0 0 6.4 6.4l1.5-2.4 4.8 1.9v3.1a2 2 0 0 1-2.1 2A16.5 16.5 0 0 1 3.2 5.6a2 2 0 0 1 2-2.1z" /></svg>
);
export const RingInIcon = ({ size = 22 }: P) => (
  <svg {...base(size)} aria-hidden="true"><path d="M5.2 5.5h3.1l1.9 4.8-2.4 1.5a11.5 11.5 0 0 0 6.4 6.4l1.5-2.4 4.8 1.9v1.6a2 2 0 0 1-2.1 2A16.5 16.5 0 0 1 3.2 7.6a2 2 0 0 1 2-2.1zM15 3v5h5M21 2l-6 6" /></svg>
);
export const UploadIcon = ({ size = 18 }: P) => (
  <svg {...base(size)} aria-hidden="true"><path d="M12 15V4M7.5 8.5 12 4l4.5 4.5M4.5 15v3.5a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V15" /></svg>
);
export const DocIcon = ({ size = 20 }: P) => (
  <svg {...base(size)} aria-hidden="true"><path d="M7 2.5h7l5 5v14H7zM14 2.5v5h5M10 13h6M10 17h4" /></svg>
);
export const ChevronIcon = ({ size = 18 }: P) => (
  <svg {...base(size)} aria-hidden="true"><path d="M9 6l6 6-6 6" /></svg>
);
export const DownloadIcon = ({ size = 18 }: P) => (
  <svg {...base(size)} aria-hidden="true"><path d="M12 4v11M7.5 10.5 12 15l4.5-4.5M5 19.5h14" /></svg>
);
export const CopyIcon = ({ size = 18 }: P) => (
  <svg {...base(size)} aria-hidden="true"><rect x="8.5" y="8.5" width="11" height="11" rx="2" /><path d="M15.5 8.5V6a1.5 1.5 0 0 0-1.5-1.5H6A1.5 1.5 0 0 0 4.5 6v8A1.5 1.5 0 0 0 6 15.5h2.5" /></svg>
);
export const CloseIcon = ({ size = 20 }: P) => (
  <svg {...base(size)} aria-hidden="true"><path d="M6 6l12 12M18 6 6 18" /></svg>
);
