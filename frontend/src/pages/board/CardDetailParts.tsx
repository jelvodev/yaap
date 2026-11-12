import type { CardDetail } from "./boardApi";
import { type BoardColumn, COLUMNS, COLUMN_LABELS, cardState } from "./boardLogic";

/** Seven segments, the ones before the card's column done and its own column marked as now. */
export function StageBar({ column }: { column: BoardColumn | null }) {
  const at = column === null ? -1 : COLUMNS.indexOf(column);
  return (
    <div className="card-detail-stages" aria-label="Stage">
      {COLUMNS.map((name, index) => (
        <div key={name} className={index < at ? "stage done" : index === at ? "stage now" : "stage"}>
          <span className="stage-bar" />
          <span className="stage-name">{COLUMN_LABELS[name]}</span>
        </div>
      ))}
    </div>
  );
}

export function StateLine({ card }: { card: CardDetail }) {
  const state = cardState(card).label;
  const who = card.next.who === "you" ? "you" : "YAAP";
  return (
    <div className="card-detail-state">
      {state && <span className={`card-detail-state-tag ${card.state}`}>{state}</span>}
      {card.next.label && (
        <span className="muted">
          Next: {card.next.label} ({who})
        </span>
      )}
    </div>
  );
}

/** A broken card: why, and the file as it is so it can be fixed on the board's branch. */
export function BrokenPart({ card }: { card: CardDetail }) {
  return (
    <section className="card-detail-section">
      <div className="card-detail-box card-detail-warning">
        <p className="form-error">{card.error ?? "The card's file cannot be read."}</p>
        {card.broken_text !== null && <pre className="card-detail-raw">{card.broken_text}</pre>}
      </div>
    </section>
  );
}

export function Footer({ card }: { card: CardDetail }) {
  const file = card.path.split("/").pop() ?? card.path;
  return (
    <p className="faint small card-detail-footer">
      Stored as <code>{file}</code> · <code>{card.location}</code> · only YAAP writes it
    </p>
  );
}
