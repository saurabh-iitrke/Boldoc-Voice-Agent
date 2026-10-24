import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// API_TARGET: where the control plane runs during `npm run dev` (default: local FastAPI).
const apiTarget = process.env.API_TARGET ?? "http://127.0.0.1:8080";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: apiTarget, changeOrigin: true, ws: true },
    },
  },
  build: {
    sourcemap: true,
    target: "es2022",
    // The Plivo Browser SDK (~980 kB) is code-split and only loads when a browser call starts.
    chunkSizeWarningLimit: 1000,
  },
});
