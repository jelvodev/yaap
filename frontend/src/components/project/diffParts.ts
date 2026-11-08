/** Split a diff of many files into one part per file, keyed by the file's path. */
export function splitDiff(diff: string): Map<string, string> {
  const parts = new Map<string, string>();
  const sections = diff.split(/^(?=diff --git )/m);
  for (const section of sections) {
    if (!section.trim()) continue;
    const target =
      /^\+\+\+ b\/(.+)$/m.exec(section)?.[1] ?? /^--- a\/(.+)$/m.exec(section)?.[1] ?? /^diff --git a\/.+ b\/(.+)$/m.exec(section)?.[1];
    if (target) parts.set(target.trim(), section);
  }
  return parts;
}

/** The part of a split diff for a path, also when the path is given without its leading folders. */
export function diffPart(parts: Map<string, string>, path: string): string {
  return parts.get(path) ?? [...parts.entries()].find(([key]) => key.endsWith(path))?.[1] ?? "";
}

/** The lines a file's diff adds and removes, without its `+++` and `---` header lines. */
export function diffStats(part: string): { added: number; removed: number } {
  let added = 0;
  let removed = 0;
  let inHunk = false;
  for (const line of part.split("\n")) {
    if (line.startsWith("@@")) inHunk = true;
    else if (!inHunk) continue;
    else if (line.startsWith("+")) added += 1;
    else if (line.startsWith("-")) removed += 1;
  }
  return { added, removed };
}

/** A file's diff from its first hunk on, without the `diff --git`, `index`, `---` and `+++` lines above it. */
export function diffBody(part: string): string {
  const lines = part.replace(/\n$/, "").split("\n");
  const first = lines.findIndex((line) => line.startsWith("@@"));
  return (first < 0 ? lines : lines.slice(first)).join("\n");
}

/** The first `limit` lines of a diff, and how many more there are. */
export function diffPreview(body: string, limit: number): { shown: string; more: number } {
  const lines = body.split("\n");
  if (lines.length <= limit) return { shown: body, more: 0 };
  return { shown: lines.slice(0, limit).join("\n"), more: lines.length - limit };
}
