import type { components } from "../../api/schema";

export type TokenRow = components["schemas"]["TokenRowOut"];
export type TokenKind = "cache_read" | "cache_write" | "input" | "output";

/** The four parts of a row, in the order of the bar. */
export const TOKEN_KINDS: readonly { kind: TokenKind; label: string }[] = [
  { kind: "cache_read", label: "Cache read" },
  { kind: "cache_write", label: "Cache write" },
  { kind: "input", label: "Uncached input" },
  { kind: "output", label: "Output" },
];

function count(tokens: number): number {
  return Math.max(0, tokens);
}

export function rowTotal(row: TokenRow): number {
  return TOKEN_KINDS.reduce((sum, { kind }) => sum + partOf(row, kind), 0);
}

function partOf(row: TokenRow, kind: TokenKind): number {
  if (kind === "cache_read") return count(row.cache_read_tokens);
  if (kind === "cache_write") return count(row.cache_write_tokens);
  return count(kind === "input" ? row.input_tokens : row.output_tokens);
}

export interface BarSegment {
  kind: TokenKind;
  label: string;
  tokens: number;
  width: number;
}

/** Widths of the bar's parts in percent of the full bar, where `max` tokens fill all of it. */
export function tokenSegments(row: TokenRow, max: number): BarSegment[] {
  return TOKEN_KINDS.map(({ kind, label }) => {
    const tokens = partOf(row, kind);
    return { kind, label, tokens, width: max > 0 ? (tokens / max) * 100 : 0 };
  });
}

/** The largest total of any row, which sets the scale of every bar. */
export function largestTotal(rows: readonly TokenRow[]): number {
  return rows.reduce((largest, row) => Math.max(largest, rowTotal(row)), 0);
}

/** A 0-1 rate as a whole percentage; null when unknown. */
export function percent(rate: number | null | undefined): string | null {
  return rate == null ? null : `${Math.round(rate * 100)}%`;
}

/** A reply row with its position in the conversation in its label, so the replies can be told apart. */
export function numberedReply(row: TokenRow, index: number): TokenRow {
  return row.label === "Reply" ? { ...row, label: `Reply ${index + 1}` } : row;
}
