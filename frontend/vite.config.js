import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: Number(process.env.PORT) || 5173,
    // Forward API calls to Flask so the browser sees one origin in dev.
    // API_PROXY points at another backend (e.g. a test copy on port 5001).
    proxy: { "/api": process.env.API_PROXY || "http://127.0.0.1:5000" },
  },
});
