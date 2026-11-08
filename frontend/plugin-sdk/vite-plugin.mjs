// The Vite side of the plugin SDK: the build emits plugin-sdk.js and plugin-sdk.d.ts next to index.html, generated
// from openapi.json, and the dev server serves them the way YAAP does, so a plugin page in a sandboxed frame (an
// opaque origin) can load the module.

import { readFile } from "node:fs/promises";
import path from "node:path";

import { SDK_DECLARATIONS, SDK_SCRIPT, generatePluginSdk } from "./generate.mjs";

const TYPES = {
  [SDK_SCRIPT]: "text/javascript; charset=utf-8",
  [SDK_DECLARATIONS]: "text/plain; charset=utf-8",
};

/** @param {{ openapi?: string }} [options] */
export function pluginSdk(options = {}) {
  let openapiFile = "";

  async function generate() {
    return generatePluginSdk(JSON.parse(await readFile(openapiFile, "utf8")));
  }

  return {
    name: "yaap-plugin-sdk",
    configResolved(config) {
      openapiFile = path.resolve(config.root, options.openapi ?? "openapi.json");
    },
    async generateBundle() {
      const { js, dts } = await generate();
      this.emitFile({ type: "asset", fileName: SDK_SCRIPT, source: js });
      this.emitFile({ type: "asset", fileName: SDK_DECLARATIONS, source: dts });
    },
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        const name = (request.url ?? "").split("?")[0].replace(/^\//, "");
        if (!(name in TYPES)) {
          next();
          return;
        }
        generate().then(
          ({ js, dts }) => {
            response.setHeader("Content-Type", TYPES[name]);
            response.setHeader("Access-Control-Allow-Origin", "*");
            response.setHeader("Cache-Control", "no-cache");
            response.end(name === SDK_SCRIPT ? js : dts);
          },
          (error) => next(error),
        );
      });
    },
  };
}
