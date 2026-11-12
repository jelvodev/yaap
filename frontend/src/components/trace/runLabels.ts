// How a run's status and worker read on screen, in the Blueprint's runs list and in the trace.

export const RUN_STATUS_LABELS: Record<string, string> = {
  running: "Running",
  complete: "Done",
  incomplete: "Stopped",
  refused: "Declined",
  error: "Error",
};

/** The coding agent CLIs a sub-run can be given as worker; a preset shows by its name. */
export const WORKER_LABELS: Record<string, string> = { claude_code: "Claude Code", codex: "Codex" };

/** How a replay's status reads. */
export const REPLAY_STATUS_LABELS: Record<string, string> = {
  queued: "Queued",
  running: "Running",
  done: "Done",
  stopped: "Stopped",
  error: "Error",
};

/** How a tool call's approval outcome reads; an outcome without an entry reads as it is. */
const APPROVAL_LABELS: Record<string, string> = { case_approved: "approved by the eval case" };

export function approvalLabel(approval: string): string {
  return APPROVAL_LABELS[approval] ?? approval;
}
