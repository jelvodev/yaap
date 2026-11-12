import type { components } from "../../api/schema";

export type ReplayLayer = components["schemas"]["ReplayLayerOut"];

const SHORT_COMMIT = 7;

function layerText(layer: ReplayLayer): string {
  if (layer.source === "variant") return `${layer.kind} at ${layer.ref ?? "its branch"} (variant)`;
  if (layer.source === "original" && layer.commit) {
    const changes = layer.dirty ? " (had uncommitted changes then)" : "";
    return `${layer.kind} at ${layer.commit.slice(0, SHORT_COMMIT)}${changes}`;
  }
  return `${layer.kind} as it is now`;
}

/** One line saying which version each Blueprint layer of a replay came from; empty for a replay stored without. */
export function blueprintLine(layers: ReplayLayer[]): string {
  if (layers.length === 0) return "";
  return `Blueprint: ${layers.map(layerText).join(", ")}`;
}
