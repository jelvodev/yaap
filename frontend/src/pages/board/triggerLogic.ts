/** Board triggers: the kinds a trigger polls and how a trigger reads in the project's board settings. Pure. */

export type TriggerKind = "ci_failed" | "new_issue" | "dependency_update" | "command_failed";

export const TRIGGER_KINDS: readonly TriggerKind[] = ["ci_failed", "new_issue", "dependency_update", "command_failed"];

export const KIND_LABELS: Record<TriggerKind, string> = {
  ci_failed: "CI fails on the default branch",
  new_issue: "A new issue with a label",
  dependency_update: "A dependency update (Dependabot, Renovate)",
  command_failed: "A scheduled command fails",
};

export const DEFAULT_LABEL = "yaap";
export const DEFAULT_CARDS_PER_DAY = 3;
export const MAX_CARDS_PER_DAY = 20;

export function isTriggerKind(value: string): value is TriggerKind {
  return (TRIGGER_KINDS as readonly string[]).includes(value);
}

/** What the trigger watches, in a few words: its kind with its label or command. */
export function triggerWhat(
  kind: TriggerKind,
  label: string | null | undefined,
  command: string | null | undefined,
): string {
  if (kind === "new_issue") return `Issues labelled "${label || DEFAULT_LABEL}"`;
  if (kind === "command_failed") return `Command: ${command ?? ""}`;
  return KIND_LABELS[kind];
}

export function cardsText(count: number): string {
  return count === 1 ? "1 card this week" : `${count} cards this week`;
}

/** The trigger's default name, from what it watches. */
export function triggerName(kind: TriggerKind, label: string, command: string): string {
  const name = triggerWhat(kind, label, command);
  return name.length <= 120 ? name : `${name.slice(0, 119)}…`;
}
