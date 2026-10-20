import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The app is served by FastAPI at / (built files go into the Python package).
// During development (`npm run dev`) API requests are proxied to the running
// Tabularium server on 127.0.0.1:8765.
const backend = "http://127.0.0.1:8765";
const proxy = {
  target: backend,
  changeOrigin: true, // Host header = backend, passes its Host allow-list
  configure: (p: { on: (event: string, cb: (req: { removeHeader: (h: string) => void }) => void) => void }) => {
    // The dev server's Origin (localhost:5173) would fail the backend's Origin check.
    p.on("proxyReq", (req) => req.removeHeader("origin"));
  },
};

export default defineConfig({
  base: "/",
  plugins: [react()],
  build: {
    outDir: "../src/tabularium/web/spa",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: { "/api": proxy },
  },
});
