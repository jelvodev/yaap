import type { components } from "../../api/schema";

export type Proposal = components["schemas"]["ImportProposalOut"];
export type Layout = NonNullable<Proposal["tool"]>;
export type Layer = Proposal["default_layer"];

/** One tool's folder layout: how the upload is read, where it comes from, and what to zip. */
export const SOURCES: { layout: Layout | "markdown"; label: string; origin: "home" | "project"; hint: string }[] = [
  {
    layout: "claude_code",
    label: "Claude Code",
    origin: "home",
    hint: "Zip your ~/.claude folder (on Windows: %USERPROFILE%\\.claude).",
  },
  { layout: "codex", label: "Codex", origin: "home", hint: "Zip ~/.codex, and ~/.agents if it holds skills." },
  { layout: "cursor", label: "Cursor", origin: "project", hint: "Zip the project's .cursor folder." },
  { layout: "copilot", label: "GitHub Copilot", origin: "project", hint: "Zip the project's .github folder." },
  { layout: "markdown", label: "Other Markdown files", origin: "project", hint: "Upload one .md file or a zip of them." },
];

export interface ImportChoice {
  include: boolean;
  kind: Proposal["kind"];
  name: string;
  layer: Layer;
  /** Ticked state of each file of a skill, in the order of the proposal's files. */
  fileOn: boolean[];
}

/** The layer an item is written to: a panel at the global level has only one. */
export function effectiveLayer(layer: Layer, levelIsGlobal: boolean): Layer {
  return levelIsGlobal ? "level" : layer;
}

/** Whether writing the item in its layer replaces something already defined there. */
export function replacesExisting(proposal: Proposal, layer: Layer): boolean {
  return layer === "global" ? proposal.exists_in_global : proposal.exists_in_level;
}

/** The first path two ticked items would both write in the same layer, or null. */
export function duplicatePath(
  choices: ImportChoice[],
  levelIsGlobal: boolean,
  pathOf: (kind: Proposal["kind"], name: string) => string,
): string | null {
  const seen = new Set<string>();
  for (const choice of choices) {
    if (!choice.include) continue;
    const path = pathOf(choice.kind, choice.name);
    const key = `${effectiveLayer(choice.layer, levelIsGlobal)}:${path}`;
    if (seen.has(key)) return path;
    seen.add(key);
  }
  return null;
}

export function validName(choice: Pick<ImportChoice, "kind" | "name">): boolean {
  return choice.kind === "instructions" || /^[a-z0-9][a-z0-9-]{0,59}$/.test(choice.name);
}

export function initialChoice(proposal: Proposal): ImportChoice {
  return {
    include: proposal.selected && proposal.blocked === null,
    kind: proposal.kind,
    name: proposal.name,
    layer: proposal.default_layer,
    fileOn: proposal.files.map((file) => file.selected),
  };
}
