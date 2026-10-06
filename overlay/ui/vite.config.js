import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The built app is served by FastAPI from overlay/ui/dist.
// In development, `npm run dev` runs on 5173 and proxies /api to the Python
// server, so the front end and back end can be worked on independently.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    // Keeps the served asset paths predictable for the FastAPI mount.
    assetsDir: "assets",
    sourcemap: false,
  },
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8800",
        changeOrigin: true,
      },
    },
  },
});
