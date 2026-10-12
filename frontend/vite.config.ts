import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// In development the API runs separately (uvicorn on port 8000); Vite forwards /api to it.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": process.env.API_URL ?? "http://127.0.0.1:8000" },
  },
  // MapLibre alone is about 1 MB minified; one chunk is fine for this app.
  build: { chunkSizeWarningLimit: 1500 },
  worker: { format: "es" },
  optimizeDeps: { exclude: ["maplibre-gl"] },
  test: { environment: "node", include: ["src/**/*.test.{ts,tsx}"] },
});
