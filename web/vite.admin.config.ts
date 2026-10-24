import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin } from "vite";

// The admin console is its own app on its own origin (admin.<host>): a separate build into dist-admin/, so the public
// site and the console never ship a line of it. `npm run dev:admin` serves it with the same tokens and fonts.
const apiTarget = process.env.API_TARGET ?? "http://127.0.0.1:8080";

const spa: Plugin = {
  name: "admin-spa",
  configureServer(server) {
    server.middlewares.use((req, _res, next) => {
      const url = req.url ?? "/";
      const asset = /\.[a-z0-9]+(\?|$)/i.test(url) || /^\/(@|src\/|node_modules\/|admin\/api)/.test(url);
      if (req.method === "GET" && !asset) req.url = "/admin.html";
      next();
    });
  },
};

export default defineConfig({
  plugins: [react(), spa],
  // its own public files (an admin favicon, the theme before first paint), not the public site's audio clips
  publicDir: "public-admin",
  server: {
    port: 5175,
    // the control plane checks the Host: point API_TARGET at it and send the admin host
    proxy: { "/admin/api": { target: apiTarget, headers: { Host: "admin.boldoc.in" } } },
  },
  build: {
    outDir: "dist-admin",
    emptyOutDir: true,
    sourcemap: false,
    target: "es2022",
    rollupOptions: { input: fileURLToPath(new URL("./admin.html", import.meta.url)) },
  },
});
