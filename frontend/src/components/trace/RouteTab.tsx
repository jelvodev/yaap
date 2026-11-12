import { Fragment, useState } from "react";

import type { RouteStopOut } from "../../api/client";
import RouteMap from "../route/RouteMap";
import { useRoute } from "../route/useRoute";
import { WhyPopover, WhyText } from "../why/WhyPopover";
import { formatCost, formatDuration } from "./traceOrder";

const LEGEND: readonly { color: string; label: string }[] = [
  { color: "var(--accent)", label: "main line" },
  { color: "#c792ea", label: "helper" },
  { color: "#f2a970", label: "tools" },
  { color: "var(--faint)", label: "afterwards" },
];

const KIND: Record<RouteStopOut["lane"], string> = {
  main: "Main line",
  tools: "Tools",
  worker: "Helper",
  afterwards: "Afterwards",
};

type OpenStep = (link: { runId: number; stepId: number }) => void;

function Drawer({ stop, onOpenStep }: { stop: RouteStopOut; onOpenStep: OpenStep }) {
  const tokens = stop.tokens;
  const label = stop.count > 1 ? `${stop.label} ×${stop.count}` : stop.label;
  const link = stop.run_id != null && stop.step_id != null ? { runId: stop.run_id, stepId: stop.step_id } : null;
  const facts: [string, string][] = [["State", stop.state]];
  if (stop.detail) facts.push(["What", stop.detail]);
  if (stop.reason) facts.push(["Reason", stop.reason]);
  if (stop.started_at) facts.push(["Started", new Date(stop.started_at).toLocaleTimeString()]);
  if (stop.duration_ms != null) facts.push(["Took", formatDuration(stop.duration_ms)]);
  if (stop.cost != null) facts.push(["Cost", formatCost(stop.cost)]);
  if (tokens) {
    facts.push([
      "Tokens",
      [
        `${tokens.new.toLocaleString()} in`,
        `${tokens.cache_read.toLocaleString()} cached`,
        `${tokens.output.toLocaleString()} out`,
      ].join(" · "),
    ]);
  }
  return (
    <aside className="route-drawer" aria-label="Chosen stop">
      <h3>{label}</h3>
      <div className="route-drawer-kind">{KIND[stop.lane] ?? "Route"}</div>
      <dl className="route-facts">
        {facts.map(([name, value]) => (
          <Fragment key={name}>
            <dt>{name}</dt>
            <dd>{value}</dd>
          </Fragment>
        ))}
      </dl>
      {stop.why && (
        <div className="route-why">
          <strong>Why</strong>
          <WhyText why={stop.why} />
        </div>
      )}
      {stop.checks.length > 0 && (
        <>
          <strong>Checks</strong>
          <ul className="route-checks">
            {stop.checks.map((check, index) => (
              <li key={`${check.kind}-${index}`}>
                {check.text} <span className={`route-check-state is-${check.state}`}>({check.state})</span>
                {check.waited_ms != null && (
                  <span className="faint"> · waited {formatDuration(check.waited_ms)}</span>
                )}{" "}
                {check.why && <WhyPopover why={check.why} />}
              </li>
            ))}
          </ul>
        </>
      )}
      {stop.items.length > 0 && (
        <ul className="route-items">
          {stop.items.map((item, index) => (
            <li key={`${index}-${item}`}>{item}</li>
          ))}
        </ul>
      )}
      {link && (
        <button type="button" className="link-button" onClick={() => onOpenStep(link)}>
          Open this step in the timeline →
        </button>
      )}
    </aside>
  );
}

/** The route of the reply drawn as a map; a stop opens to what happened there, why, and what it cost. */
export default function RouteTab({
  conversationId,
  messageId,
  onOpenStep,
}: {
  conversationId: number;
  messageId: number;
  onOpenStep: OpenStep;
}) {
  const { route, error } = useRoute(conversationId, messageId);
  const [chosen, setChosen] = useState<string | null>(null);
  if (error) return <p className="notice notice-error trace-pad">{error}</p>;
  if (!route) return <p className="faint trace-pad">Loading…</p>;
  if (route.stops.length === 0) return <p className="faint trace-pad">This reply recorded no route.</p>;
  const stop = route.stops.find((entry) => entry.id === chosen) ?? route.stops[route.stops.length - 1];
  return (
    <div className="route-tab">
      <div className="route-canvas">
        <RouteMap stops={route.stops} mode="full" selectedId={stop.id} onSelect={setChosen} />
        <div className="route-legend">
          {LEGEND.map(({ color, label }) => (
            <span key={label}>
              <i style={{ background: color }} />
              {label}
            </span>
          ))}
        </div>
      </div>
      <Drawer stop={stop} onOpenStep={onOpenStep} />
    </div>
  );
}
