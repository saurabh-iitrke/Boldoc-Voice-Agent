/** BolDoc's mark: a page whose first line is your voice (orange), then the writing that follows. */
export function Mark({ size = 20 }: { size?: number }) {
  return (
    <svg width={size * 0.83} height={size} viewBox="0 0 20 24" aria-hidden="true">
      <rect x="1" y="1" width="18" height="22" rx="3.5" fill="var(--card)" stroke="var(--ink)" strokeWidth="1.6" />
      <rect x="4.6" y="6" width="10.8" height="3.4" rx="1.7" fill="var(--voice)" />
      <rect x="4.6" y="11.8" width="10.8" height="2.2" rx="1.1" fill="var(--noise)" />
      <rect x="4.6" y="16.4" width="6.8" height="2.2" rx="1.1" fill="var(--noise-2)" />
    </svg>
  );
}
