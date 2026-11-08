import type { components } from "../api/schema";

/** What a model list or the Workers listing says about whether a choice can run (ModelOut, WorkerOut). */
type Marks = Pick<components["schemas"]["ModelOut"], "available" | "allowed" | "unavailable_reason">;

/** Whether a model can be used now: ok, no working connection or key, or forbidden by the model policy. */
export type ModelState =
  | { kind: "ok" }
  | { kind: "no_key"; reason: string | null }
  | { kind: "not_allowed"; reason: string | null };

export function modelState(model: Marks): ModelState {
  const reason = model.unavailable_reason ?? null;
  // The policy comes first: adding a key does not make a forbidden model usable.
  if (model.allowed === false) return { kind: "not_allowed", reason };
  if (model.available === false) return { kind: "no_key", reason };
  return { kind: "ok" };
}

/** The short mark of a model that cannot be used, as a badge shows it; empty when the model can be used. */
export function stateMark(state: ModelState): string {
  if (state.kind === "not_allowed") return "not allowed";
  if (state.kind === "no_key") return "no key";
  return "";
}

/** The mark in parentheses after a name in a list of choices, with its leading space; empty when usable. */
export function stateSuffix(state: ModelState): string {
  const mark = stateMark(state);
  return mark ? ` (${mark})` : "";
}
