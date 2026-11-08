import type { Plugin } from "vite";

/** Emits plugin-sdk.js and plugin-sdk.d.ts in the build and serves them in development. */
export declare function pluginSdk(options?: { openapi?: string }): Plugin;
