import type { components } from "../../api/schema";

export type BuiltinItem = components["schemas"]["BuiltinItemOut"];
export type OverriddenBy = components["schemas"]["OverriddenByOut"];

/** The built-in item as the file a user's layer would hold: front matter with the description, then the body. */
export function builtinFileText(item: Pick<BuiltinItem, "description" | "instructions">): string {
  const description = item.description.replace(/\s+/g, " ").trim();
  // A double-quoted JSON string is valid YAML, so descriptions with colons or quotes stay one line.
  const value = /^[A-Za-z0-9][^:#"'\\]*$/.test(description) ? description : JSON.stringify(description);
  return `---\ndescription: ${value}\n---\n\n${item.instructions.trim()}\n`;
}

/** Items of one kind, in the order the server sent them. */
export function itemsOfKind(items: BuiltinItem[], kind: BuiltinItem["kind"]): BuiltinItem[] {
  return items.filter((item) => item.kind === kind);
}

/** Money spent this month as a short label; very small amounts do not show as zero. */
export function formatCost(cost: number): string {
  if (cost === 0) return "$0.00";
  if (cost < 0.01) return "< $0.01";
  return `$${cost.toFixed(2)}`;
}
