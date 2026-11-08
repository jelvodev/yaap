import type { BlueprintItem } from "../../api/client";

export type ItemKind = BlueprintItem["kind"];

export const KINDS: { kind: ItemKind; title: string; singular: string; folder: string; hint: string }[] = [
  { kind: "instructions", title: "Instructions", singular: "instructions", folder: "", hint: "Always in the prompt, in full." },
  {
    kind: "standard",
    title: "Standards",
    singular: "standard",
    folder: "standards",
    hint: "The summary and rules are always in the prompt; the full text is loaded on demand.",
  },
  {
    kind: "engineering",
    title: "Engineering",
    singular: "engineering standard",
    folder: "engineering",
    hint: "Longer standards: the summary and rules are always in the prompt, the full text on demand.",
  },
  { kind: "skill", title: "Skills", singular: "skill", folder: "skills", hint: "Reusable ways of working, loaded by name." },
  { kind: "command", title: "Commands", singular: "command", folder: "commands", hint: "Prompt templates invoked as /name." },
  {
    kind: "agent",
    title: "Agents",
    singular: "agent",
    folder: "agents",
    hint: "Roles a conversation can work as: a role prompt, a preset and the tools it may use.",
  },
];

export function kindInfo(kind: ItemKind) {
  return KINDS.find((entry) => entry.kind === kind)!;
}

export const MAX_RULES = 7;
export const NAME_PATTERN = /^[a-z0-9][a-z0-9-]{0,59}$/;

export const TEMPLATES: Record<ItemKind, string> = {
  instructions: "",
  standard: "---\nsummary: \nrules:\n  - \n---\n\n",
  engineering: "---\nsummary: \nrules:\n  - \n---\n\n",
  skill: "---\ndescription: \n---\n\n",
  command: "---\ndescription: \n---\n\n",
  agent: "---\ndescription: \n---\n\nRole prompt.\n",
};

export const EXTENSION_TEMPLATE = "---\nextends: true\n---\n\n";

export function pathFor(kind: ItemKind, name: string): string {
  if (kind === "instructions") return "instructions.md";
  if (kind === "skill") return `skills/${name}/SKILL.md`;
  return `${kindInfo(kind).folder}/${name}.md`;
}

/** A rough token count (about four characters per token), to show what an item costs in the prompt. */
export function estimateTokens(text: string): number {
  return Math.ceil(text.trim().length / 4);
}

// ---- Inheritance state of an item at one level ----

export type ItemState = "defines" | "overrides" | "extends" | "orphan" | "off" | "inherits" | "absent";

export function itemState(item: BlueprintItem | undefined): ItemState {
  if (!item) return "absent";
  if (item.orphan_extension) return "orphan";
  if (item.disabled && item.defined_here) return "off";
  if (item.extended_here) return "extends";
  if (item.defined_here) return item.inherited_from ? "overrides" : "defines";
  if (item.disabled) return "off";
  return "inherits";
}

export const STATE_LABELS: Record<ItemState, string> = {
  defines: "defines it",
  overrides: "overrides",
  extends: "extends",
  orphan: "extends nothing",
  off: "switched off",
  inherits: "inherits",
  absent: "not defined",
};

/** One line on where an item's content comes from, seen from the level being viewed. */
export function stateLabel(item: BlueprintItem, levelName: string): string {
  if (item.disabled) return item.defined_here ? `Switched off in ${levelName}` : `Switched off in ${item.source_label}`;
  if (item.orphan_extension) return "Adds to a skill that does not exist above · not used";
  if (item.extended_here) return `Extended in ${levelName}`;
  if (item.defined_here) return item.inherited_from ? `Overridden in ${levelName}` : `${levelName} only`;
  return item.source === "global" ? "Shared · Global" : `Inherited from ${item.source_label}`;
}

// ---- Front matter: summary and rules edited as fields, everything else kept as written ----

/** One top-level key of the front matter with its lines; summary and rules are edited, the rest kept verbatim. */
type HeaderEntry = { key: "summary" } | { key: "rules" } | { key: "other"; lines: string[] };

export interface StandardDocument {
  summary: string;
  rules: string[];
  /** The front matter in its original order, so saving keeps other keys where they were. */
  entries: HeaderEntry[];
  body: string;
}

function unquote(value: string): string {
  const trimmed = value.trim();
  if (trimmed.startsWith('"') && trimmed.endsWith('"') && trimmed.length >= 2) {
    try {
      return JSON.parse(trimmed) as string;
    } catch {
      return trimmed.slice(1, -1);
    }
  }
  if (trimmed.startsWith("'") && trimmed.endsWith("'") && trimmed.length >= 2) {
    return trimmed.slice(1, -1).replace(/''/g, "'");
  }
  return trimmed;
}

/** A scalar written over several lines: plain or quoted lines fold into one with spaces; | keeps the line breaks. */
function scalar(first: string, continuation: string[]): string {
  const head = first.trim();
  const rest = continuation.map((line) => line.trim());
  if (head.startsWith("|")) return rest.join("\n");
  if (head.startsWith(">")) return rest.filter(Boolean).join(" ");
  return unquote([head, ...rest].filter(Boolean).join(" "));
}

/** A YAML scalar: plain when that is safe, otherwise double-quoted (JSON strings are valid YAML). */
function yamlScalar(value: string): string {
  const plain =
    value !== "" &&
    !/^[\s\-?:,[\]{}#&*!|>'"%@`]/.test(value) &&
    !/:\s|\s#|:$/.test(value) &&
    !/\s$/.test(value) &&
    !/\n/.test(value) &&
    !/^(true|false|null|yes|no|on|off|~|[-+]?[\d.]+)$/i.test(value);
  return plain ? value : JSON.stringify(value);
}

const TOP_LEVEL_KEY = /^([A-Za-z_][\w-]*):(.*)$/;

/**
 * Split a standard into its summary, rules, other front matter and body. Returns null when the front matter does not
 * look like simple YAML (lines before the first key, nested values under rules), so the raw editor is used instead.
 */
export function parseStandard(content: string): StandardDocument | null {
  const normalised = content.replace(/\r\n/g, "\n");
  const match = /^---\n([\s\S]*?)\n---\n?([\s\S]*)$/.exec(normalised);
  if (!match) return { summary: "", rules: [], entries: [{ key: "summary" }, { key: "rules" }], body: normalised };
  const [, header, body] = match;

  const groups: { key: string; value: string; lines: string[] }[] = [];
  for (const line of header.split("\n")) {
    const top = TOP_LEVEL_KEY.exec(line);
    if (top) groups.push({ key: top[1], value: top[2], lines: [line] });
    else if (groups.length) groups[groups.length - 1].lines.push(line);
    else if (line.trim()) return null;
  }

  let summary = "";
  const rules: string[] = [];
  const entries: HeaderEntry[] = [];
  for (const group of groups) {
    const continuation = group.lines.slice(1);
    if (group.key === "summary") {
      summary = scalar(group.value, continuation);
      entries.push({ key: "summary" });
    } else if (group.key === "rules") {
      const inline = group.value.trim();
      if (inline && inline !== "[]") return null;
      let item: { first: string; lines: string[] } | null = null;
      const items: { first: string; lines: string[] }[] = [];
      for (const line of continuation) {
        const dash = /^(\s*)-(\s+|$)(.*)$/.exec(line);
        if (dash) {
          item = { first: dash[3], lines: [] };
          items.push(item);
        } else if (!line.trim()) continue;
        else if (item && /^\s/.test(line)) item.lines.push(line);
        else return null;
      }
      rules.push(...items.map((entry) => scalar(entry.first, entry.lines)));
      entries.push({ key: "rules" });
    } else {
      entries.push({ key: "other", lines: group.lines });
    }
  }
  if (!entries.some((entry) => entry.key === "summary")) entries.unshift({ key: "summary" });
  if (!entries.some((entry) => entry.key === "rules")) {
    const at = entries.findIndex((entry) => entry.key === "summary");
    entries.splice(at + 1, 0, { key: "rules" });
  }
  return { summary, rules, entries, body: body.replace(/^\n/, "") };
}

export function serializeStandard(document: StandardDocument): string {
  const rules = document.rules.map((rule) => rule.trim()).filter(Boolean);
  const header: string[] = [];
  for (const entry of document.entries) {
    if (entry.key === "summary") header.push(`summary: ${yamlScalar(document.summary.trim())}`);
    else if (entry.key === "rules") {
      if (rules.length > 0) header.push("rules:", ...rules.map((rule) => `- ${yamlScalar(rule)}`));
    } else header.push(...entry.lines.filter((line) => line.trim() !== ""));
  }
  return `---\n${header.join("\n")}\n---\n\n${document.body.replace(/^\n+/, "")}`;
}

/** The body of a document, without its front matter. */
export function documentBody(content: string): string {
  const match = /^---\r?\n[\s\S]*?\r?\n---\r?\n?([\s\S]*)$/.exec(content);
  return (match ? match[1] : content).trim();
}

// ---- What an item puts into the system prompt (mirrors backend/app/blueprint/context.py) ----

export function promptText(item: BlueprintItem, body: string): { always: string; onDemand: string | null } {
  if (item.kind === "instructions") return { always: `# General instructions\n\n${body}`, onDemand: null };
  if (item.kind === "standard" || item.kind === "engineering") {
    const label = item.kind === "standard" ? "standard" : "engineering standard";
    const lines = [`## ${item.name} (${label})`];
    if (item.summary) lines.push(item.summary);
    lines.push(...item.rules.map((rule) => `- ${rule}`));
    return { always: lines.join("\n"), onDemand: body || null };
  }
  const description = item.summary ? `: ${item.summary}` : "";
  return { always: `- ${item.kind} \`${item.name}\`${description}`, onDemand: body || null };
}

export const ON_DEMAND_TOOL: Partial<Record<ItemKind, string>> = {
  standard: "load_standard",
  engineering: "load_standard",
  skill: "load_skill",
};
