import { canReadAloud, readAloud, stopReading, useReadAloud } from "./readAloud";
import "./speech.css";

/** "Read aloud" on a reply; "Stop reading" while it is being read. */
export default function ReadAloudButton({ messageId }: { messageId: number }) {
  const { messageId: speaking, error } = useReadAloud();
  if (!canReadAloud()) return null;
  const active = speaking === messageId;
  return (
    <button
      type="button"
      className={active ? "read-aloud-active" : undefined}
      aria-pressed={active}
      title={active ? undefined : (error ?? "Read this reply aloud with your browser's voice; code and tool calls are skipped")}
      onClick={() => (active ? stopReading() : void readAloud(messageId))}
    >
      {active ? "Stop reading" : "Read aloud"}
    </button>
  );
}
