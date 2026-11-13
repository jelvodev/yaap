/** Pure helpers that read and write the Blueprint page's URL state, and the list filters and sync marks. */
import type { BlueprintItem, GitRepository } from "../../api/client";
import type { Level } from "../../components/workbench/LevelPicker";
import { type ItemKind, KINDS } from "./model";

export type Filter = "all" | "here" | "inherited" | "overridden" | "off";
export type Main = "item" | "new" | "import" | "github" | "package" | "starter" | "template";

export const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "here", label: "This level" },
  { value: "inherited", label: "Inherited" },
  { value: "overridden", label: "Overridden" },
  { value: "off", label: "Off" },
];

export function matchesFilter(item: BlueprintItem, filter: Filter): boolean {
  switch (filter) {
    case "here":
      return item.defined_here || item.extended_here;
    case "inherited":
      return !item.defined_here && !item.extended_here;
    case "overridden":
      return item.defined_here && Boolean(item.inherited_from);
    case "off":
      return item.disabled;
    default:
      return true;
  }
}

/** The value of ?builtin= while the Built-in layer is open without an item chosen. */
export const BUILTIN_LAYER = "all";

/** The Built-in item named in ?builtin=, or null while only the layer is open. */
export function builtinItemOf(params: URLSearchParams): string | null {
  const value = params.get("builtin");
  return value && value !== BUILTIN_LAYER ? value : null;
}

/** The level a link asks for: ?level=global|environment|project with environment and project ids. */
export function levelFromParams(params: URLSearchParams): Level | null {
  const environmentId = Number(params.get("environment"));
  const projectId = Number(params.get("project"));
  switch (params.get("level")) {
    case "global":
      return { kind: "global" };
    case "environment":
      return environmentId ? { kind: "environment", environmentId } : null;
    case "project":
      return environmentId && projectId ? { kind: "project", environmentId, projectId } : null;
    default:
      return null;
  }
}

export function levelParams(level: Level): Record<string, string> {
  if (level.kind === "global") return { level: "global" };
  if (level.kind === "environment") return { level: "environment", environment: String(level.environmentId) };
  return { level: "project", environment: String(level.environmentId), project: String(level.projectId) };
}

export function kindOfPath(path: string): ItemKind | null {
  if (path === "instructions.md") return "instructions";
  const folder = path.split("/")[0];
  return KINDS.find((entry) => entry.folder === folder && entry.folder !== "")?.kind ?? null;
}

/** Sync state of a repository as a short mark for the rail. */
export function syncMark(repository: GitRepository | undefined): { text: string; title: string; warn: boolean } | null {
  if (!repository || repository.error) return null;
  if (repository.changes.length) return { text: `●${repository.changes.length}`, title: `${repository.changes.length} uncommitted`, warn: true };
  if (repository.ahead) return { text: `↑${repository.ahead}`, title: `${repository.ahead} commits not pushed`, warn: true };
  if (repository.behind) return { text: `↓${repository.behind}`, title: `${repository.behind} commits to pull`, warn: true };
  if (!repository.remote_url) return { text: "", title: "No remote", warn: false };
  return { text: "✓", title: "In sync with its remote", warn: false };
}
