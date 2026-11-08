// Generates the plugin SDK from YAAP's OpenAPI description: plugin-sdk.js (one ES module) and plugin-sdk.d.ts.
//
// The frontend build runs this through the Vite plugin in vite-plugin.mjs, so the SDK always matches the API in
// openapi.json; YAAP serves both files from the build at /plugin-sdk.js and /plugin-sdk.d.ts. By hand:
//
//   node plugin-sdk/generate.mjs openapi.json <folder>

import { readFile, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import openapiTS, { astToString } from "openapi-typescript";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const METHODS = ["get", "post", "put", "patch", "delete"];
export const SDK_SCRIPT = "plugin-sdk.js";
export const SDK_DECLARATIONS = "plugin-sdk.d.ts";

/** "GET /api/v1/environments" for every operation of the description, sorted. */
export function routesOf(openapi) {
  const routes = [];
  for (const [route, item] of Object.entries(openapi.paths ?? {})) {
    for (const method of METHODS) if (item[method]) routes.push(`${method.toUpperCase()} ${route}`);
  }
  return routes.sort();
}

function fill(template, values) {
  let text = template;
  for (const [name, value] of Object.entries(values)) {
    if (!text.includes(name)) throw new Error(`The SDK template has no ${name}.`);
    text = text.replaceAll(name, value);
  }
  return text;
}

/** The SDK's two files, as text, for an OpenAPI description (an object). */
export async function generatePluginSdk(openapi) {
  const [runtime, declarations, protocolText] = await Promise.all([
    readFile(path.join(HERE, "runtime.js"), "utf8"),
    readFile(path.join(HERE, "declarations.d.ts"), "utf8"),
    readFile(path.join(HERE, "protocol.json"), "utf8"),
  ]);
  const protocol = JSON.parse(protocolText);
  const version = String(openapi.info?.version ?? "unknown");
  const header =
    `// YAAP plugin SDK for API ${version}. Generated from YAAP's OpenAPI description by plugin-sdk/generate.mjs;\n` +
    "// do not edit. Documentation: docs/plugins.md.\n\n";
  const js = fill(runtime, {
    __API_VERSION__: JSON.stringify(version),
    __ROUTES__: JSON.stringify(routesOf(openapi), null, 2),
    __PROTOCOL__: JSON.stringify({ name: protocol.name, version: protocol.version, messages: protocol.messages }),
  });
  const api = astToString(await openapiTS(openapi));
  const dts = fill(declarations, {
    __API_VERSION__: JSON.stringify(version),
    __THEME_TOKENS__: protocol.themeTokens.map((token) => JSON.stringify(token)).join(" | "),
  });
  return { js: header + js, dts: header + api + "\n" + dts };
}

/** Read an OpenAPI file and write the SDK's files into a folder. */
export async function writePluginSdk(openapiFile, folder) {
  const openapi = JSON.parse(await readFile(openapiFile, "utf8"));
  const { js, dts } = await generatePluginSdk(openapi);
  await mkdir(folder, { recursive: true });
  await writeFile(path.join(folder, SDK_SCRIPT), js);
  await writeFile(path.join(folder, SDK_DECLARATIONS), dts);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [openapiFile, folder] = process.argv.slice(2);
  if (!openapiFile || !folder) {
    console.error("Usage: node plugin-sdk/generate.mjs <openapi.json> <folder>");
    process.exit(2);
  }
  await writePluginSdk(openapiFile, folder);
}
