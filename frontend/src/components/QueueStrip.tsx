import type { components } from "../api/schema";

export type QueuedMessage = components["schemas"]["QueuedMessageOut"];

/** Messages written while a reply runs, shown above the composer. */
export default function QueueStrip({
  items,
  onSendNow,
  onSendAll,
  onRemove,
  onSteer,
}: {
  items: QueuedMessage[];
  onSendNow: (item: QueuedMessage) => void;
  onSendAll: () => void;
  onRemove: (item: QueuedMessage) => void;
  // Give a queued message to the running agent now; not for messages with attachments.
  onSteer?: (item: QueuedMessage) => void;
}) {
  if (items.length === 0) return null;
  return (
    <div className="queue-strip" aria-label="Queued messages">
      <div className="queue-head">
        <span>
          {items.length} queued · sent when the reply ends
        </span>
        {items.length > 1 && (
          <button type="button" className="link-button" onClick={onSendAll}>
            Send all as one
          </button>
        )}
      </div>
      <ul>
        {items.map((item) => (
          <li key={item.id}>
            <span className="queue-text">
              {item.text || "(attachment)"}
              {item.attachment_ids.length > 0 && (
                <span className="faint">
                  {" "}
                  · {item.attachment_ids.length} attachment{item.attachment_ids.length === 1 ? "" : "s"}
                </span>
              )}
            </span>
            {onSteer && item.attachment_ids.length === 0 && (
              <button
                type="button"
                className="link-button"
                onClick={() => onSteer(item)}
                title="Send this to YAAP now without stopping: it reads it with the result of its next action"
              >
                Steer
              </button>
            )}
            <button type="button" className="link-button" onClick={() => onSendNow(item)} title="Stop the reply and send this now">
              Send now
            </button>
            <button type="button" className="icon-button" aria-label="Remove from the queue" onClick={() => onRemove(item)}>
              ×
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
