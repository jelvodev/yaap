import { useContext } from "react";

import type { Message } from "../../api/client";
import { OutputContext } from "../message/contexts";
import { formatCost } from "../trace/traceOrder";
import { formatClock } from "./routeLayout";
import { useRoute } from "./useRoute";
import "./route.css";

/** "Route · 1:08 · $0.06": the first of the chips behind a reply; it opens the trace on its Route tab. */
export default function RouteChip({ message, onOpen }: { message: Message; onOpen: () => void }) {
  const conversationId = useContext(OutputContext);
  if (conversationId === null) return null;
  return <LoadedRouteChip conversationId={conversationId} message={message} onOpen={onOpen} />;
}

function LoadedRouteChip({
  conversationId,
  message,
  onOpen,
}: {
  conversationId: number;
  message: Message;
  onOpen: () => void;
}) {
  const { route } = useRoute(conversationId, message.id, true);
  if (!route || route.stops.length === 0) return null;
  const facts = [
    route.duration_ms != null ? formatClock(route.duration_ms) : null,
    route.cost != null ? formatCost(route.cost) : null,
  ].filter(Boolean);
  return (
    <div className="behind-item">
      <button
        type="button"
        className="behind-chip route-chip"
        title="Show the route of this reply"
        onClick={onOpen}
      >
        <svg width="26" height="10" viewBox="0 0 26 10" aria-hidden="true">
          <path d="M2 5H24" stroke="var(--accent)" strokeWidth="2" fill="none" />
          <circle cx="3" cy="5" r="2.5" fill="var(--accent)" />
          <circle cx="23" cy="5" r="2.5" fill="var(--accent)" />
        </svg>
        {["Route", ...facts].join(" · ")}
      </button>
    </div>
  );
}
