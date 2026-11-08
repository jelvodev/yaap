/** The confirmation before uncommitted changes are discarded, in place of the button that asked for it. */
export default function DiscardConfirm({
  question,
  busy,
  onCancel,
  onDiscard,
}: {
  /** What is lost and how it can come back (`discardQuestion`). */
  question: string;
  busy: boolean;
  onCancel: () => void;
  onDiscard: () => void;
}) {
  return (
    <div className="cp-discard-confirm" role="alertdialog" aria-label="Discard changes">
      <p className="small">{question}</p>
      <div className="cp-discard-buttons">
        <button type="button" className="button button-danger" disabled={busy} onClick={onDiscard}>
          {busy ? "Discarding…" : "Discard"}
        </button>
        <button type="button" className="button button-ghost" disabled={busy} onClick={onCancel}>
          Keep
        </button>
      </div>
    </div>
  );
}
