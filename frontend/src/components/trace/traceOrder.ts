// The order and placement of a trace's rows (tracing). Pure functions, tested with `npm run test:unit`;
// only type imports, so Node runs this file as it is.
import type { components } from "../../api/schema";

export type TraceRun = components["schemas"]["TraceRunOut"];
export type TraceStep = components["schemas"]["TraceStepOut"];

export type TimelineRow =
  | { kind: "run"; key: string; run: TraceRun; depth: number }
  | { kind: "step"; key: string; step: TraceStep; run: TraceRun; depth: number };

function startOf(item: { started_at: string }): number {
  return Date.parse(item.started_at);
}

/**
 * A run and its sub-runs at any depth as rows, in the order things happened: each run's header, then its steps, with
 * each sub-run (and everything under it) right after the step during which it started, usually its `delegate` call.
 */
export function timelineRows(root: TraceRun, depth = 0): TimelineRow[] {
  const rows: TimelineRow[] = [{ kind: "run", key: `run-${root.id}`, run: root, depth }];
  const children = [...root.children].sort((a, b) => startOf(a) - startOf(b) || a.id - b.id);
  let next = 0;
  for (const step of root.steps) {
    while (next < children.length && startOf(children[next]) < startOf(step)) {
      rows.push(...timelineRows(children[next], depth + 1));
      next += 1;
    }
    rows.push({ kind: "step", key: `step-${step.id}`, step, run: root, depth });
  }
  for (const child of children.slice(next)) rows.push(...timelineRows(child, depth + 1));
  return rows;
}

/** Every run of the tree, each before its sub-runs. */
export function allRuns(root: TraceRun): TraceRun[] {
  return [root, ...root.children.flatMap(allRuns)];
}

export interface TimeSpan {
  start: number;
  end: number;
}

/** From the first start to the last end of anything in the trace, in milliseconds since the epoch. */
export function timeSpan(root: TraceRun): TimeSpan {
  let start = Number.POSITIVE_INFINITY;
  let end = Number.NEGATIVE_INFINITY;
  for (const run of allRuns(root)) {
    start = Math.min(start, startOf(run));
    end = Math.max(end, run.finished_at ? Date.parse(run.finished_at) : startOf(run));
    for (const step of run.steps) {
      start = Math.min(start, startOf(step));
      end = Math.max(end, startOf(step) + step.duration_ms);
    }
  }
  return Number.isFinite(start) ? { start, end: Math.max(end, start + 1) } : { start: 0, end: 1 };
}

/** Where a step's bar sits on the timeline, as percentages of the trace's span; at least a sliver wide. */
export function barOf(step: TraceStep, span: TimeSpan): { left: number; width: number } {
  const total = span.end - span.start;
  const left = Math.min(100, Math.max(0, ((startOf(step) - span.start) / total) * 100));
  const width = Math.max(0.5, Math.min(100 - left, (step.duration_ms / total) * 100));
  return { left, width };
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return "";
  if (ms < 1000) return `${ms} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

/** A step's cost is often a fraction of a cent, so small amounts keep four decimals. */
export function formatCost(cost: number | null | undefined): string {
  if (cost == null) return "";
  return cost < 0.01 ? `$${cost.toFixed(4)}` : `$${cost.toFixed(2)}`;
}

/** A tool call of a reply, named by its tool and which call of that tool it was (0 for the first). */
export interface ToolCallRef {
  name: string;
  occurrence: number;
}

/**
 * The step of the reply's own run that recorded a tool call. Steps keep no tool call id, so the call is found by its
 * tool and its place among the run's calls of that tool, in the order they were recorded; null when there is none.
 */
export function stepForToolCall(root: TraceRun, call: ToolCallRef): TraceStep | null {
  const steps = root.steps
    .filter((step) => step.kind === "tool" && step.tool === call.name)
    .sort((a, b) => a.position - b.position);
  return steps[call.occurrence] ?? null;
}
