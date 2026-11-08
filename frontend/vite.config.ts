import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

import { pluginSdk } from "./plugin-sdk/vite-plugin.mjs";

export default defineConfig({
  // The plugin SDK (/plugin-sdk.js and /plugin-sdk.d.ts) is generated from openapi.json by every build.
  plugins: [react(), pluginSdk()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
