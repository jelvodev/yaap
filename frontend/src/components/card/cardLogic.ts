// Pure rules of the card shown in the chat. Keep it free of imports so `node --test` can load it.

export const STAGES = ["inbox", "idea", "design", "plan", "build", "review", "done"] as const;

export type Stage = (typeof STAGES)[number];

export type CardActionName = "plan" | "build" | "continue" | "stop" | "review" | "mark_done";

export interface NextButton {
  action: CardActionName;
  label: string;
}

export interface StepProgress {
  status: string;
}

export interface NextInput {
  /** The `next.action` the board names for the card. */
  nextAction: string;
  /** The status of the card's plan, null without one. */
  planStatus: string | null;
  /** The review the card has already, set when an earlier review failed. */
  reviewId: number | null;
  /** The card points at a plan that no longer exists. */
  planMissing: boolean;
}

/** Where a column sits in the stage bar; -1 for a card without a column. */
export function stageIndex(column: string | null): number {
  return STAGES.findIndex((stage) => stage === column);
}

/** The buttons for a card's next step: usually one, none when the step is not the user's to take here. */
export function nextButtons(input: NextInput): NextButton[] {
  if (input.planMissing) return [{ action: "plan", label: "Plan" }];
  if (input.planStatus === "building") return [{ action: "stop", label: "Stop" }];
  switch (input.nextAction) {
    case "plan":
      return [{ action: "plan", label: "Plan" }];
    case "approve":
      return [{ action: "build", label: "Approve and build" }];
    case "continue":
      return [{ action: "continue", label: "Continue" }];
    case "review":
      return [{ action: "review", label: input.reviewId === null ? "Review" : "Review again" }];
    case "judge":
      return [{ action: "mark_done", label: "Mark done" }];
    default:
      return [];
  }
}

/** The step a continued build resumes at: the first one that is neither done nor skipped, 1-based. */
export function resumeStep(steps: StepProgress[]): number {
  const index = steps.findIndex((step) => step.status !== "done" && step.status !== "skipped");
  return index === -1 ? steps.length : index + 1;
}

/** The next-step label, with where the build resumes when the step is to continue it. */
export function nextLabel(label: string, nextAction: string, steps: StepProgress[] | null, planMissing: boolean): string {
  if (planMissing) return "Plan missing";
  if (nextAction !== "continue" || !steps || steps.length === 0) return label;
  return `${label} (step ${resumeStep(steps)} of ${steps.length})`;
}
