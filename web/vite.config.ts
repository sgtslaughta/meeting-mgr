/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    // The API is a separate origin in dev; proxying keeps EventSource and
    // <audio> same-origin so no CORS config is needed anywhere.
    proxy: { "/meetings": "http://localhost:8000" },
  },
  test: {
    environment: "jsdom", globals: true, setupFiles: "./tests/setup.ts",
    exclude: ["**/node_modules/**", "e2e/**"],
  },
});
