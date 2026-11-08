import type { ModelInfo } from "../api/client";

/** The label of a model in a plain list of choices, with its vendor and whether it can be used. */
export function modelLabel(model: ModelInfo): string {
  const vendor = model.provider_label ? ` · ${model.provider_label}` : "";
  return `${model.display_name}${vendor}${model.available ? "" : " (no key)"}`;
}

/**
 * The options of a model select, grouped per vendor. A model the environment cannot use is shown but disabled, so it
 * is clear what adding a key would offer; the current value stays selectable so an existing choice is not lost.
 */
export default function ModelOptions({ models, current }: { models: ModelInfo[]; current?: string }) {
  const groups = new Map<string, ModelInfo[]>();
  for (const model of models) {
    const label = model.provider_label || model.provider;
    groups.set(label, [...(groups.get(label) ?? []), model]);
  }
  return (
    <>
      {[...groups.entries()].map(([label, items]) => (
        <optgroup key={label} label={label}>
          {items.map((model) => (
            <option
              key={model.id}
              value={model.id}
              disabled={!model.available && model.id !== current}
              title={model.unavailable_reason ?? undefined}
            >
              {model.display_name}
              {model.available ? "" : " (no key)"}
            </option>
          ))}
        </optgroup>
      ))}
    </>
  );
}
