// Pure rules of the evidence shown on a reviewed card. Keep it free of imports so `node --test` can load it.

export type CriterionStatus = "passed" | "failed" | "judged_met" | "judged_not_met" | "not_checked";

export interface StatusView {
  mark: string;
  label: string;
  /** Drives the colour: the criterion is met, not met, or open. */
  tone: "met" | "unmet" | "open";
}

const STATUS_VIEWS: Record<CriterionStatus, StatusView> = {
  passed: { mark: "✓", label: "passed", tone: "met" },
  judged_met: { mark: "✓", label: "judged by the reviewer", tone: "met" },
  failed: { mark: "✗", label: "failed", tone: "unmet" },
  judged_not_met: { mark: "✗", label: "judged", tone: "unmet" },
  not_checked: { mark: "–", label: "not checked", tone: "open" },
};

export function statusView(status: CriterionStatus): StatusView {
  return STATUS_VIEWS[status];
}

/** Whether a criterion is one the user can send back to Design. */
export function isUnmet(status: CriterionStatus): boolean {
  return status === "failed" || status === "judged_not_met";
}

export interface CheckLike {
  ref: string;
}

/** The check a ref names: an exact `ref` match, else `check:<n>` as an index into the list. */
export function findCheck<T extends CheckLike>(checks: T[], ref: string): T | null {
  const exact = checks.find((check) => check.ref === ref);
  if (exact) return exact;
  const match = /^check:(\d+)$/.exec(ref);
  if (!match) return null;
  return checks[Number(match[1])] ?? null;
}

/** The checks of one plan step, by their `step:<n>` ref. */
export function checksOfStep<T extends CheckLike>(checks: T[], ref: string): T[] {
  return checks.filter((check) => check.ref === ref);
}

/** The step number of a `step:<n>` ref, null for any other ref. */
export function stepNumber(ref: string): number | null {
  const match = /^step:(\d+)$/.exec(ref);
  return match ? Number(match[1]) : null;
}

/** "3 files, +40 −2". */
export function diffStatText(stat: { files: number; insertions: number; deletions: number }): string {
  return `${stat.files} ${stat.files === 1 ? "file" : "files"}, +${stat.insertions} −${stat.deletions}`;
}

const OUTCOME_LABELS: Record<string, string> = {
  passed: "passed",
  failed: "failed",
  not_run: "not run",
  stopped: "stopped",
  error: "error",
};

export function outcomeLabel(outcome: string): string {
  return OUTCOME_LABELS[outcome] ?? outcome;
}
