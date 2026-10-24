/* The admin console's saved theme before the first paint (same key and rule as src/admin/theme.ts). A file, not an
   inline script: the admin host's Content-Security-Policy allows scripts from itself only. */
try {
  if (localStorage.getItem("boldoc.admin.theme") === "dark") {
    document.documentElement.dataset.theme = "dark";
    document.querySelector('meta[name="theme-color"]').content = "#141413";
  }
} catch (e) { /* storage blocked: light */ }
