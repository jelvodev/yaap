// What the board page decides without the server: grouping, the version filter, progress, budget colour, whether a
// card may be dropped in a column, and how a card's state reads. Pure, so `boardLogic.test.mjs` can load it in Node;
// keep it free of imports. `canDrop` mirrors the move table of the backend's board flow (`move` for actor "user");
// the server still decides, this only spares a request that would be refused.

export const COLUMNS = ["inbox", "idea", "design", "plan", "build", "review", "done"] as const;
export type BoardColumn = (typeof COLUMNS)[number];

export const COLUMN_LABELS: Record<BoardColumn, string> = {
  inbox: "Inbox",
  idea: "Idea",
  design: "Design",
  plan: "Plan",
  build: "Build",
  review: "Review",
  done: "Done",
};

/** What one column does, under its name. */
export const COLUMN_NOTES: Record<BoardColumn, string> = {
  inbox: "From the agent, triggers and imports. Accept or drop.",
  idea: "Notes to work out into a specification.",
  design: "Specified; ready to plan.",
  plan: "A read-only plan with a check per step.",
  build: "Built step by step in a copy of the project.",
  review: "The diff and the checks, reviewed.",
  done: "",
};

type PlanStatus = "draft" | "approved" | "queued" | "building" | "interrupted" | "done" | "failed" | "cancelled";
type CardStateName = "broken" | "working" | "failed" | "interrupted" | "waiting" | "idle";
type BudgetLevel = "none" | "ok" | "warn" | "over";

/** The fields of a card the logic below reads; the API's card has them all. */
export interface CardFacts {
  id: number;
  column: BoardColumn | null;
  version: string | null;
  broken: boolean;
  state: CardStateName;
  plan: { status: PlanStatus | null } | null;
  review: "none" | "running" | "done";
}

export type VersionFilter = { kind: "all" } | { kind: "unscheduled" } | { kind: "version"; name: string };

/** Cards per column in board order; broken cards (no column) are left out, the page lists them apart. */
export function groupByColumn<T extends CardFacts>(cards: readonly T[]): Record<BoardColumn, T[]> {
  const groups = Object.fromEntries(COLUMNS.map((column) => [column, [] as T[]])) as Record<BoardColumn, T[]>;
  for (const card of cards) {
    if (card.column !== null) groups[card.column].push(card);
  }
  return groups;
}

/** The cards a version filter shows. Inbox is not triaged yet, so its cards show under every filter. */
export function filterByVersion<T extends CardFacts>(cards: readonly T[], filter: VersionFilter): T[] {
  return cards.filter((card) => {
    if (filter.kind === "all" || card.column === "inbox") return true;
    if (filter.kind === "unscheduled") return !card.version;
    return card.version === filter.name;
  });
}

/** How to keep a filter as text (for localStorage) and read it back; anything unreadable is "all". */
export function filterKey(filter: VersionFilter): string {
  return filter.kind === "version" ? `version:${filter.name}` : filter.kind;
}

export function parseFilterKey(value: string | null): VersionFilter {
  if (value === "unscheduled") return { kind: "unscheduled" };
  if (value?.startsWith("version:") && value.length > "version:".length) {
    return { kind: "version", name: value.slice("version:".length) };
  }
  return { kind: "all" };
}

export interface Progress {
  done: number;
  total: number;
  /** 0 to 100, rounded; 0 for a version without cards. */
  percent: number;
}

export function versionProgress(done: number, total: number): Progress {
  const percent = total > 0 ? Math.round((Math.min(done, total) / total) * 100) : 0;
  return { done, total, percent };
}

/** The budget meter's colour, as the backend's budget level: warn from 80 %, over above 100 %, none without budget. */
export function budgetColour(spent: number, budget: number | null): BudgetLevel {
  if (budget === null) return "none";
  if (budget === 0) return spent === 0 ? "ok" : "over";
  if (spent > budget) return "over";
  if (spent >= 0.8 * budget) return "warn";
  return "ok";
}

export type DropCheck = { ok: true } | { ok: false; reason: string };

function position(column: BoardColumn): number {
  return COLUMNS.indexOf(column);
}

/** Whether the user may move the card into `target` by hand, with the flow's sentence when not. */
export function canDrop(card: CardFacts, target: BoardColumn): DropCheck {
  if (card.broken || card.column === null) {
    return { ok: false, reason: "The card's file cannot be read; fix it on the board's branch first." };
  }
  const from = card.column;
  const plan = card.plan?.status ?? null;
  if (target === from) return { ok: false, reason: "The card is already there." };
  if (position(target) < position(from)) {
    if (plan === "building") return { ok: false, reason: "Stop the build first." };
    if (target === "plan" && plan !== "draft") {
      return { ok: false, reason: "Use Send back: the plan must be made again." };
    }
    return { ok: true };
  }
  const pair = `${from}>${target}`;
  if (pair === "inbox>idea" || pair === "inbox>design" || pair === "idea>design" || pair === "design>plan") {
    return { ok: true };
  }
  if (pair === "plan>build") return { ok: false, reason: "Use Approve and build on the plan." };
  if (pair === "build>review") {
    return plan === "done" ? { ok: true } : { ok: false, reason: "The build has not finished." };
  }
  if (pair === "review>done") {
    if (card.review === "done") return { ok: true };
    return { ok: false, reason: "No review has run. Use Mark done to skip it." };
  }
  return { ok: false, reason: "Cards move one column at a time." };
}

/** The next column a card may be moved into by hand, for *Move to <next>*; null when the flow refuses it. */
export function nextColumn(card: CardFacts): BoardColumn | null {
  if (card.column === null) return null;
  const next = COLUMNS[position(card.column) + 1];
  return next !== undefined && canDrop(card, next).ok ? next : null;
}

export interface StateLook {
  /** The tile's class modifier; "waiting" draws the yellow edge. */
  tone: CardStateName;
  /** A short line for the tile; empty for an idle card. */
  label: string;
}

/** How a card's state reads on its tile. */
export function cardState(card: Pick<CardFacts, "state">): StateLook {
  switch (card.state) {
    case "broken":
      return { tone: "broken", label: "Cannot be read" };
    case "working":
      return { tone: "working", label: "Working" };
    case "failed":
      return { tone: "failed", label: "Build failed" };
    case "interrupted":
      return { tone: "interrupted", label: "Interrupted" };
    case "waiting":
      return { tone: "waiting", label: "Waiting for you" };
    default:
      return { tone: "idle", label: "" };
  }
}

/** How a card's origin reads on its tile; empty for a card the user made on the board. */
export function originLabel(type: string | null): string {
  switch (type) {
    case "agent":
      return "by agent";
    case "trigger":
      return "trigger";
    case "issue":
      return "issue";
    case "chat":
      return "from chat";
    case "review":
      return "from a review";
    case "learning":
      return "from learning";
    default:
      return "";
  }
}

/** Whether card text from this origin is the user's own; other text is shown as outside text (`plain`). */
export function trustedOrigin(type: string | null): boolean {
  return type === "user" || type === "chat";
}

/** A button of a card's column part in the card detail: a stage action, or a step the board takes itself. */
export type ColumnButton =
  | "specify"
  | "plan"
  | "move_to_idea"
  | "build"
  | "edit_plan"
  | "ask_change"
  | "stop"
  | "continue"
  | "send_back"
  | "review"
  | "mark_done";

/** What decides a card's column buttons: its column, its plan and review, and the cards it waits for. */
export interface ColumnButtonFacts {
  column: BoardColumn | null;
  broken: boolean;
  planStatus: PlanStatus | null;
  /** The card names a plan that does not exist any more. */
  planMissing: boolean;
  review: "none" | "running" | "done";
  /** Cards this one waits for that are not done yet. */
  blockedBy: readonly number[];
}

/**
 * The buttons of a card's column, in order, the first being the step that is due. Mirrors the backend's actions: a
 * button that would be refused for the card as it is (a second review while one runs, approving while cards it waits
 * for are open) is left out; the server still decides.
 */
export function columnButtons(facts: ColumnButtonFacts): ColumnButton[] {
  if (facts.broken) return [];
  switch (facts.column) {
    case "idea":
      return ["specify"];
    case "design":
      return ["plan", "move_to_idea"];
    case "plan":
      if (facts.planMissing || facts.planStatus === null || facts.planStatus === "cancelled") return ["plan"];
      if (facts.planStatus !== "draft") return [];
      return facts.blockedBy.length > 0 ? ["edit_plan", "ask_change"] : ["build", "edit_plan", "ask_change"];
    case "build":
      if (facts.planStatus === "building" || facts.planStatus === "queued") return ["stop"];
      if (facts.planStatus === "failed" || facts.planStatus === "interrupted") return ["continue", "send_back"];
      if (facts.planStatus === "done" || facts.planStatus === "cancelled") return ["send_back"];
      return [];
    case "review":
      return facts.review === "running" ? ["mark_done"] : ["review", "mark_done"];
    default:
      return [];
  }
}
