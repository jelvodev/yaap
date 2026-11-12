import { useEffect, useState } from "react";

import type { RouteStopOut } from "../../api/client";
import { formatCost } from "../trace/traceOrder";
import RouteMap from "./RouteMap";
import { formatClock } from "./routeLayout";
import { waitingText } from "./routeStages";
import "./route.css";

/** The route so far while a reply streams: it grows stop by stop and says what the reply waits for. */
export default function RouteStrip({ stops }: { stops: readonly RouteStopOut[] }) {
  const startedAt = stops.find((stop) => stop.started_at)?.started_at;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  if (stops.length === 0) return null;

  const waiting = stops.find((stop) => stop.state === "waiting");
  const latest = stops[stops.length - 1];
  const cost = stops.reduce((sum, stop) => sum + (stop.cost ?? 0), 0);
  const elapsed = startedAt ? formatClock(now - Date.parse(startedAt)) : null;
  return (
    <div className="route-strip" aria-live="polite">
      <RouteMap stops={stops} mode="compact" />
      <div className="route-strip-status">
        {waiting ? (
          <span>
            <span className="route-strip-waiting">Waiting for you</span> · {waitingText(waiting.label, waiting.detail)}
          </span>
        ) : (
          <span>{latest.label}</span>
        )}
        <span>
          {[elapsed, cost > 0 ? `${formatCost(cost)} so far` : null].filter(Boolean).join(" · ")}
        </span>
      </div>
    </div>
  );
}
