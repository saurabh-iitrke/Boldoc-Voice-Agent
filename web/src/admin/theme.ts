/** The admin console's theme: light unless picked, kept on this device. public-admin/theme.js applies the same rule
 * before the first paint (no inline script: the admin host's CSP allows none). */
export type Theme = "light" | "dark";

const KEY = "boldoc.admin.theme";
const BAR: Record<Theme, string> = { light: "#f3f3f1", dark: "#141413" };   // the browser's bar: --panel

export function savedTheme(): Theme {
  try { return localStorage.getItem(KEY) === "dark" ? "dark" : "light"; } catch { return "light"; }
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", BAR[theme]);
}

export function chooseTheme(theme: Theme): void {
  try { localStorage.setItem(KEY, theme); } catch { /* private mode: this visit only */ }
  applyTheme(theme);
}
