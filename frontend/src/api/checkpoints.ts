/** Tool calls that can change project files; each gets a checkpoint before it runs. */
export const CHANGING_TOOLS = new Set(["write_file", "edit_file", "rename_symbol", "run_command", "start_process", "import_attachment", "install_skill"]);

/** JSON with sorted keys and no spaces, as Python's json.dumps(sort_keys=True, separators=(",", ":")) writes it. */
function stableStringify(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value) ?? "null";
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
  const entries = Object.keys(value as Record<string, unknown>)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${stableStringify((value as Record<string, unknown>)[key])}`);
  return `{${entries.join(",")}}`;
}

/** The key the server stores with a checkpoint: FNV-1a (64 bit) over the tool name and its arguments. */
export function callKey(tool: string, input: Record<string, unknown>): string {
  const bytes = new TextEncoder().encode(`${tool}\n${stableStringify(input)}`);
  let hash = 0xcbf29ce484222325n;
  for (const byte of bytes) {
    hash ^= BigInt(byte);
    hash = (hash * 0x100000001b3n) & 0xffffffffffffffffn;
  }
  return hash.toString(16).padStart(16, "0");
}
