// What the Waiting for me page groups and where each row leads. Pure, so a test can load it in Node; keep it free of
// imports.

/** The row kinds the backend sends (app/board/waiting.py). */
export type WaitingKind =
  | "approve_plan"
  | "judge_review"
  | "answer_question"
  | "approve_action"
  | "continue_build"
  | "new_inbox";

export type WaitingAction = "approve" | "open" | "answer" | "continue" | "triage";

/** The fields of a row that grouping and its target read. */
export interface WaitingRowFacts {
  kind: string;
  project_id: number | null;
  card_id: number | null;
  conversation_id: number | null;
}

/** The sections top to bottom. A tool approval comes first: its run waits and denies it after half an hour. */
export const SECTIONS: readonly { kind: WaitingKind; title: string }[] = [
  { kind: "approve_action", title: "Approve an action" },
  { kind: "approve_plan", title: "Approve a plan" },
  { kind: "judge_review", title: "Judge a review" },
  { kind: "answer_question", title: "Answer a question" },
  { kind: "new_inbox", title: "New in Inbox" },
  { kind: "continue_build", title: "Continue a build" },
];

export const ACTION_LABELS: Record<WaitingAction, string> = {
  approve: "Approve",
  open: "Open",
  answer: "Answer",
  continue: "Continue",
  triage: "Triage",
};

export interface WaitingSection<T> {
  kind: WaitingKind;
  title: string;
  items: T[];
}

/** The rows by section, in the sections' order, keeping the backend's order within one; empty sections and kinds
 * this page does not know are left out. */
export function groupWaiting<T extends WaitingRowFacts>(items: readonly T[]): WaitingSection<T>[] {
  return SECTIONS.map((section) => ({ ...section, items: items.filter((item) => item.kind === section.kind) })).filter(
    (section) => section.items.length > 0,
  );
}

/** Where a row's action is done: the card on its board, else the conversation, else the project's board. */
export type WaitingTarget =
  | { kind: "card"; projectId: number; cardId: number }
  | { kind: "conversation"; conversationId: number }
  | { kind: "board"; projectId: number };

export function waitingTarget(item: WaitingRowFacts): WaitingTarget | null {
  if (item.project_id !== null && item.card_id !== null) {
    return { kind: "card", projectId: item.project_id, cardId: item.card_id };
  }
  if (item.conversation_id !== null) return { kind: "conversation", conversationId: item.conversation_id };
  if (item.project_id !== null) return { kind: "board", projectId: item.project_id };
  return null;
}

/** The share of a version's cards that are done, as a whole percentage for the progress bar. */
export function donePercent(done: number, total: number): number {
  if (total <= 0) return 0;
  return Math.round((Math.min(done, total) / total) * 100);
}
