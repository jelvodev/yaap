import type { CardDetailHandle } from "./useCardDetail";
import type { CardDetail } from "./boardApi";
import { COLUMN_LABELS, nextColumn, originLabel } from "./boardLogic";

/** Inbox: where the card came from and the triage buttons. */
export function InboxPart({
  card,
  currentVersion,
  handle,
  onDrop,
}: {
  card: CardDetail;
  currentVersion: string | null;
  handle: CardDetailHandle;
  onDrop: () => void;
}) {
  const label = originLabel(card.origin.type);
  async function accept(version: string | null): Promise<void> {
    if (await handle.save({ version })) await handle.move("idea");
  }
  return (
    <section className="card-detail-section">
      <h3>Where it came from</h3>
      <div className="card-detail-box">
        <div>{label || "Added on the board"}</div>
        {card.origin.ref && <div className="faint small">{card.origin.ref}</div>}
      </div>
      <div className="card-detail-actions">
        {currentVersion !== null && (
          <button
            type="button"
            className="button button-primary"
            disabled={handle.busy}
            onClick={() => void accept(currentVersion)}
          >
            Accept into {currentVersion}
          </button>
        )}
        <button type="button" className="button" disabled={handle.busy} onClick={() => void accept(null)}>
          Accept, not scheduled
        </button>
        <button type="button" className="button button-danger" disabled={handle.busy} onClick={onDrop}>
          Drop
        </button>
      </div>
    </section>
  );
}

/** Design and later: read-only, with the hand move to the next column when the flow allows it. */
export function LaterPart({ card, handle }: { card: CardDetail; handle: CardDetailHandle }) {
  const next = nextColumn(card);
  return (
    <div className="card-detail-actions">
      {next !== null && (
        <button type="button" className="button" disabled={handle.busy} onClick={() => void handle.move(next)}>
          Move to {COLUMN_LABELS[next]}
        </button>
      )}
      {/* Stage actions: the buttons of each column (Specify, Plan, Approve and build, Continue, Review, Mark done,
          Send back) are added here, built from components/card/. */}
    </div>
  );
}
