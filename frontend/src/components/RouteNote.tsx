import type { Message } from "../api/client";

type ReplyRoute = NonNullable<Message["route"]>;

/** Which of the preset's models answered a routed reply, and why. */
export default function RouteNote({ route }: { route: ReplyRoute }) {
  return (
    <div className="message-route" title={`Routed to ${route.model} · effort ${route.effort}`}>
      <span className="note-chip">{route.model_name}</span>
      <span>{route.reason}</span>
    </div>
  );
}
