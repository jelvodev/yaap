import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

import { version } from "./package.json";
import { pluginSdk } from "./plugin-sdk/vite-plugin.mjs";

// The version of this build, so a page that stays open across an update can tell (updates/useReloadNotice.ts).
export default defineConfig({
  define: { __YAAP_VERSION__: JSON.stringify(version) },
  // The plugin SDK (/plugin-sdk.js and /plugin-sdk.d.ts) is generated from openapi.json by every build.
  plugins: [react(), pluginSdk()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
