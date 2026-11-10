// What the Models list says about a downloadable model. Pure, so it is tested without a browser
// (modelState.test.mjs); its only import is of types, which Node strips.

import type { components } from "../api/schema";

export type LocalModel = components["schemas"]["LocalModelOut"];

/** A size in decimal units, as the download is announced: 2.3 GB, 486 MB. */
export function sizeText(bytes: number): string {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  if (bytes >= 1e6) return `${Math.round(bytes / 1e6)} MB`;
  return `${Math.max(1, Math.round(bytes / 1e3))} KB`;
}

/** Whole percent downloaded, 0 to 100. */
export function percent(model: Pick<LocalModel, "size" | "downloaded">): number {
  if (model.size <= 0) return 0;
  return Math.min(100, Math.floor((model.downloaded / model.size) * 100));
}

/** The state in words; a download in progress shows how far it is. */
export function stateText(model: LocalModel): string {
  switch (model.state) {
    case "ready":
      return "ready";
    case "downloading":
      return `downloading, ${percent(model)}% of ${sizeText(model.size)}`;
    case "failed":
      return "failed";
    default:
      return "not downloaded";
  }
}

/** Whether the list should be read again soon: a download is on its way. */
export function anyDownloading(models: readonly LocalModel[]): boolean {
  return models.some((model) => model.state === "downloading");
}
