// Where the stops of an answer's route are drawn (pure, tested with `npm run test:unit`). Positions are in cells:
// `col` runs left to right along the main line (row 0), tool branches hang below it, worker lines run above it and
// the stops after the answer hang below the tools. Only type imports, so Node runs this file as it is.
import type { components } from "../../api/schema";

export type RouteStop = components["schemas"]["RouteStopOut"];
export type RouteLane = RouteStop["lane"];

const KNOWN_STAGES: readonly string[] = [
  "message",
  "route",
  "context",
  "model",
  "tool",
  "worker",
  "answer",
  "afterwards",
  "note",
];
const KNOWN_LANES: readonly string[] = ["main", "tools", "worker", "afterwards"];

export interface PlacedStop {
  stop: RouteStop;
  col: number;
  row: number;
  /** The lane it is drawn in; a lane this version does not know is drawn on the main line. */
  lane: RouteLane;
  /** The text under the stop: "read_file ×3" for stacked calls. */
  label: string;
  /** A stage or lane this version does not know: a plain stop. */
  generic: boolean;
}

export interface RouteSegment {
  key: string;
  lane: RouteLane;
  /** Cell positions of the corners, drawn as one line. */
  points: readonly (readonly [number, number])[];
  dashed: boolean;
}

export interface RouteLayout {
  stops: PlacedStop[];
  segments: RouteSegment[];
  /** The columns and rows the drawing covers, to size it. */
  cols: number;
  minRow: number;
  maxRow: number;
}

/** The label under a stop; calls of one tool that were stacked into it say how many. */
export function stopLabel(stop: Pick<RouteStop, "label" | "count">): string {
  return stop.count > 1 ? `${stop.label} ×${stop.count}` : stop.label;
}

function laneOf(stop: RouteStop): RouteLane {
  return KNOWN_LANES.includes(stop.lane) ? stop.lane : "main";
}

/** Group by key, keeping the order of first appearance. */
function groupBy<T>(items: readonly T[], keyOf: (item: T) => string): T[][] {
  const groups = new Map<string, T[]>();
  for (const item of items) {
    const key = keyOf(item);
    groups.set(key, [...(groups.get(key) ?? []), item]);
  }
  return [...groups.values()];
}

type Point = readonly [number, number];

/**
 * Place the stops of a route. The main line keeps the order it came in; a tool branch, a worker line or a stop after
 * the answer leaves from its parent stop (the last main-line stop before it when the parent is unknown).
 */
export function layoutRoute(stops: readonly RouteStop[]): RouteLayout {
  const lanes = new Map(stops.map((stop) => [stop.id, laneOf(stop)]));
  const mainStops = stops.filter((stop) => lanes.get(stop.id) === "main");
  const mainCol = new Map(mainStops.map((stop, index) => [stop.id, index]));
  const lastCol = mainStops.length - 1;
  const placed: PlacedStop[] = [];
  const segments: RouteSegment[] = [];
  const place = (stop: RouteStop, col: number, row: number) =>
    placed.push({
      stop,
      col,
      row,
      lane: lanes.get(stop.id) ?? "main",
      label: stopLabel(stop),
      generic: !KNOWN_STAGES.includes(stop.stage) || !KNOWN_LANES.includes(stop.lane),
    });

  mainStops.forEach((stop, index) => {
    place(stop, index, 0);
    if (index > 0) {
      segments.push({ key: `main:${stop.id}`, lane: "main", points: [[index - 1, 0], [index, 0]], dashed: false });
    }
  });

  // The main-line column a branch leaves from: its parent's, else the last main-line stop that came before it.
  const parentCols = new Map<string, number>();
  let before = 0;
  for (const stop of stops) {
    const col = mainCol.get(stop.id);
    if (col !== undefined) before = col;
    else parentCols.set(stop.id, (stop.parent ? mainCol.get(stop.parent) : undefined) ?? before);
  }
  const colOf = (stop: RouteStop) => parentCols.get(stop.id) ?? 0;
  const inLane = (lane: RouteLane) => stops.filter((stop) => lanes.get(stop.id) === lane);

  // Branch stops spread evenly between their parent and the next main-line stop, where the line rejoins.
  const spread = (lane: RouteLane, group: RouteStop[], row: number) => {
    const parentCol = colOf(group[0]);
    const cols = group.map((_, index) => parentCol + (index + 1) / (group.length + 1));
    group.forEach((stop, index) => place(stop, cols[index], row));
    const end: Point[] =
      parentCol + 1 <= lastCol
        ? [[parentCol + 1, row], [parentCol + 1, 0]]
        : [[cols[cols.length - 1], row]];
    segments.push({ key: `${lane}:${group[0].id}`, lane, points: [[parentCol, 0], [parentCol, row], ...end], dashed: false });
  };

  // Stops that share a parent share one branch; each worker line has a row of its own above the main line.
  const toolGroups = groupBy(inLane("tools"), (stop) => String(colOf(stop)));
  toolGroups.forEach((group) => spread("tools", group, 1));
  groupBy(inLane("worker"), (stop) => stop.line ?? stop.id).forEach((group, index) =>
    spread("worker", group, -(index + 1)),
  );

  // After the answer: a dashed line from the answer, below the tools, running on to the right.
  const afterStops = inLane("afterwards");
  if (afterStops.length > 0) {
    const from = colOf(afterStops[0]);
    const row = toolGroups.length > 0 ? 2 : 1;
    afterStops.forEach((stop, index) => place(stop, from + 1 + index, row));
    segments.push({
      key: `afterwards:${afterStops[0].id}`,
      lane: "afterwards",
      points: [[from, 0], [from, row], [from + afterStops.length, row]],
      dashed: true,
    });
  }

  const rows = placed.map((entry) => entry.row);
  return {
    stops: placed,
    segments,
    cols: Math.max(1, ...placed.map((entry) => Math.ceil(entry.col) + 1)),
    minRow: Math.min(0, ...rows),
    maxRow: Math.max(0, ...rows),
  };
}

/** A duration as a clock, "1:08". */
export function formatClock(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}
