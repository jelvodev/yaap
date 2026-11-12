import type { BehindLabel, CallWhy } from "../../api/client";
import { formatCost } from "../trace/traceOrder.ts";

/** The labels shown as chips (at most `max`) and the rest, which sit behind a "more" chip. */
export function splitChips<T>(labels: T[], max = 7): { shown: T[]; more: T[] } {
  return { shown: labels.slice(0, max), more: labels.slice(max) };
}

/** A cost for a chip: nothing when unknown, and "free" when the reply cost nothing. */
export function costText(cost: number | null): string {
  if (cost === null) return "";
  return cost === 0 ? "free" : formatCost(cost);
}

/** The facts of a label for its popover, one per line: cost and tokens when present. */
export function labelLines(label: Pick<BehindLabel, "cost" | "tokens">): string[] {
  const lines: string[] = [];
  if (label.cost !== null) lines.push(`Cost: ${costText(label.cost)}`);
  if (label.tokens !== null) lines.push(`Tokens: ${label.tokens.toLocaleString("en-US")}`);
  return lines;
}

/** The reason of the call with this key, if one was recorded. */
export function callWhyFor<T extends Pick<CallWhy, "call_key">>(calls: T[], key: string): T | undefined {
  return calls.find((call) => call.call_key === key);
}
