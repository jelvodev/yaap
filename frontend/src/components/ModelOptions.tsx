import type { ModelInfo } from "../api/client";
import { modelState, stateSuffix } from "./modelState";

/** The label of a model in a plain list of choices, with its vendor and whether it can be used. */
export function modelLabel(model: ModelInfo): string {
  const vendor = model.provider_label ? ` · ${model.provider_label}` : "";
  return `${model.display_name}${vendor}${stateSuffix(modelState(model))}`;
}

/**
 * The options of a model select, grouped per vendor. A model that cannot be used (no key, or not allowed by the model
 * policy) is shown but disabled, its reason as the title, so it is clear what is missing; the current value stays
 * selectable so an existing choice is not lost.
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
          {items.map((model) => {
            const state = modelState(model);
            return (
              <option
                key={model.id}
                value={model.id}
                disabled={state.kind !== "ok" && model.id !== current}
                title={state.kind === "ok" ? undefined : (state.reason ?? undefined)}
              >
                {model.display_name}
                {stateSuffix(state)}
              </option>
            );
          })}
        </optgroup>
      ))}
    </>
  );
}
