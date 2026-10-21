import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: /api/* is proxied to the FastAPI server, so the UI works with no CORS
// setup. For a deployed build, set VITE_API_URL to the API origin.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
