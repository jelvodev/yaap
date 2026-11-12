import { type KeyboardEvent, useEffect, useRef } from "react";

import { type PlacedStop, type RouteStop, layoutRoute } from "./routeLayout";
import "./route.css";

interface Metrics {
  stepX: number;
  stepY: number;
  radius: number;
  pad: number;
  labels: boolean;
}

const METRICS: Record<"compact" | "full", Metrics> = {
  compact: { stepX: 52, stepY: 20, radius: 5.5, pad: 14, labels: false },
  full: { stepX: 96, stepY: 66, radius: 9, pad: 48, labels: true },
};

interface RouteMapProps {
  stops: readonly RouteStop[];
  /** The small drawing of the live strip, or the large one of the trace. */
  mode: "compact" | "full";
  selectedId?: string | null;
  onSelect?: (id: string) => void;
}

function describe(placed: PlacedStop): string {
  const { stop } = placed;
  const state = stop.state === "ran" ? "" : `, ${stop.state}`;
  return `${placed.label}${state}`;
}

function Stop({ placed, metrics, x, y, selected, onSelect }: StopProps) {
  const { stop } = placed;
  const { radius } = metrics;
  const press = (event: KeyboardEvent) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    onSelect?.(stop.id);
  };
  const interactive = onSelect !== undefined;
  const above = placed.row < 0;
  return (
    <g
      className={`route-stop lane-${placed.lane} state-${stop.state}${selected ? " is-selected" : ""}`}
      role={interactive ? "button" : undefined}
      tabIndex={interactive ? 0 : undefined}
      aria-label={interactive ? describe(placed) : undefined}
      aria-pressed={interactive ? selected : undefined}
      onClick={interactive ? () => onSelect(stop.id) : undefined}
      onKeyDown={interactive ? press : undefined}
    >
      <title>{describe(placed)}</title>
      {stop.state === "waiting" && <circle className="route-halo" cx={x} cy={y} r={radius + (metrics.labels ? 8 : 5)} />}
      <circle className="route-ring" cx={x} cy={y} r={radius} />
      {stop.state !== "skipped" && <circle className="route-dot" cx={x} cy={y} r={radius / 2.6} />}
      {metrics.labels && (
        <text x={x} y={above ? y - radius - 9 : y + radius + 17} textAnchor="middle">
          {placed.label}
        </text>
      )}
    </g>
  );
}

interface StopProps {
  placed: PlacedStop;
  metrics: Metrics;
  x: number;
  y: number;
  selected: boolean;
  onSelect?: (id: string) => void;
}

/** The route drawn as lines and stops. It keeps its size and scrolls sideways when the space is narrower. */
export default function RouteMap({ stops, mode, selectedId = null, onSelect }: RouteMapProps) {
  const metrics = METRICS[mode];
  const layout = layoutRoute(stops);
  const box = useRef<HTMLDivElement | null>(null);
  const x = (col: number) => metrics.pad + col * metrics.stepX;
  const y = (row: number) => metrics.pad + (row - layout.minRow) * metrics.stepY;
  const width = x(layout.cols - 1) + metrics.pad;
  const height = y(layout.maxRow) + metrics.pad;

  // A route that is still growing keeps its newest stop in view.
  useEffect(() => {
    if (mode === "compact" && box.current) box.current.scrollLeft = box.current.scrollWidth;
  }, [mode, stops.length]);

  return (
    <div className={`route-map route-map-${mode}`} ref={box}>
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Route of the answer">
        {layout.segments.map((segment) => (
          <polyline
            key={segment.key}
            className={`route-line lane-${segment.lane}${segment.dashed ? " is-dashed" : ""}`}
            points={segment.points.map(([col, row]) => `${x(col)},${y(row)}`).join(" ")}
          />
        ))}
        {layout.stops.map((placed) => (
          <Stop
            key={placed.stop.id}
            placed={placed}
            metrics={metrics}
            x={x(placed.col)}
            y={y(placed.row)}
            selected={placed.stop.id === selectedId}
            onSelect={onSelect}
          />
        ))}
      </svg>
    </div>
  );
}
