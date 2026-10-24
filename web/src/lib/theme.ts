/** The console's theme: light unless the user picks dark (kept on this device). The call page follows it (its
 * document stays paper); the public site and the document viewer (paper, like the downloaded file) are always light.
 * index.html applies the same rules before the first paint, so a dark console never flashes light. */
export type Theme = "light" | "dark";

const KEY = "boldoc.theme";
const BAR: Record<Theme, string> = { light: "#f3f3f1", dark: "#141413" };   // the browser's bar: --panel

export function savedTheme(): Theme {
  try { return localStorage.getItem(KEY) === "dark" ? "dark" : "light"; } catch { return "light"; }
}

/** The signed-in console's own pages. */
export function isConsolePath(path: string): boolean {
  return path === "/app" || (path.startsWith("/app/") && !path.startsWith("/app/documents/"));
}

/** Marks the page as the console or not (the dark tokens apply only there) and colours the browser's bar to match. */
export function applyArea(path: string, theme: Theme = savedTheme()): void {
  const root = document.documentElement;
  const inConsole = isConsolePath(path);
  if (inConsole) root.dataset.area = "app";
  else delete root.dataset.area;
  root.dataset.theme = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", BAR[inConsole ? theme : "light"]);
}

/** The header's switch: remembered on this device. The colours flip at once: for two frames nothing transitions, so
 * no button fades through the other theme's colours (styles/base.css). */
export function chooseTheme(theme: Theme): void {
  try { localStorage.setItem(KEY, theme); } catch { /* private mode: this visit only */ }
  const root = document.documentElement;
  root.dataset.switching = "";
  applyArea(window.location.pathname, theme);
  requestAnimationFrame(() => requestAnimationFrame(() => { delete root.dataset.switching; }));
}
