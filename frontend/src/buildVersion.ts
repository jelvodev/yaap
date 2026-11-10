// The version this build of the frontend was made with (vite.config.ts writes it in from package.json).
declare const __YAAP_VERSION__: string;

export const BUILD_VERSION: string = typeof __YAAP_VERSION__ === "string" ? __YAAP_VERSION__ : "";
