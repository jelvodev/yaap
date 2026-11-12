// Pure helpers for the plan card: progress, and the editor's text form of files and checks. No imports, so the
// unit tests run them with node --test.

export type PlanStatusName =
  | "draft"
  | "approved"
  | "queued"
  | "building"
  | "interrupted"
  | "done"
  | "failed"
  | "cancelled";

export interface CheckLike {
  text: string;
  run?: string | null;
}

export interface StepLike {
  status: string;
}

/** How many steps are done (or skipped) out of all steps. */
export function planProgress(steps: readonly StepLike[]): { done: number; total: number } {
  const done = steps.filter((step) => step.status === "done" || step.status === "skipped").length;
  return { done, total: steps.length };
}

const STATUS_LABELS: Record<PlanStatusName, string> = {
  draft: "Draft",
  approved: "Approved",
  queued: "Queued",
  building: "Building",
  interrupted: "Interrupted",
  done: "Done",
  failed: "Failed",
  cancelled: "Discarded",
};

export function statusLabel(status: PlanStatusName): string {
  return STATUS_LABELS[status];
}

/** A discarded or replaced plan folds to one line. */
export function isFolded(status: PlanStatusName): boolean {
  return status === "cancelled";
}

const RUN_MARK = "run:";

/** One check as an editor line: its text, then the command after `run:`. */
export function checkLine(check: CheckLike): string {
  return check.run ? `${check.text} ${RUN_MARK} ${check.run}` : check.text;
}

/** An editor line as a check: everything after the first `run:` is the command. */
export function parseCheckLine(line: string): CheckLike {
  const at = line.indexOf(RUN_MARK);
  if (at === -1) return { text: line.trim(), run: null };
  const text = line.slice(0, at).trim();
  const run = line.slice(at + RUN_MARK.length).trim();
  return { text: text || run, run: run || null };
}

/** Lines of a textarea, trimmed, without empty ones. */
export function nonEmptyLines(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

/** The list with the item at `index` moved by `offset` (-1 up, 1 down); unchanged at the ends. */
export function moved<T>(items: readonly T[], index: number, offset: number): T[] {
  const target = index + offset;
  if (index < 0 || index >= items.length || target < 0 || target >= items.length) return [...items];
  const copy = [...items];
  const [item] = copy.splice(index, 1);
  copy.splice(target, 0, item);
  return copy;
}
