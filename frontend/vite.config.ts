import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// В разработке запросы к API проксируются на локальный бэкенд,
// в Docker тот же путь /api обслуживает nginx.
const api = process.env.VITE_API_PROXY ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": api,
      "/health": api,
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
});
