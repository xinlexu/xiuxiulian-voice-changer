import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The UI is served by the Python engine (app/xxl/server.py). `npm run build`
// writes straight into app/web. `npm run dev` proxies the API to a running app.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "../app/web",
    emptyOutDir: true,
    chunkSizeWarningLimit: 900,
    // fixed file names: an update overwrites the old files instead of piling up new ones
    rollupOptions: {
      output: { entryFileNames: "assets/app.js", chunkFileNames: "assets/[name].js", assetFileNames: "assets/[name][extname]" },
    },
  },
  server: {
    host: "127.0.0.1",
    port: 5174,
    proxy: { "/api": "http://127.0.0.1:18674" },
  },
});
