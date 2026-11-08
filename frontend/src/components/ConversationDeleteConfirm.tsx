/** The confirmation before a conversation is deleted for good, in the menu that asked for it (sidebar or header). */
export default function ConversationDeleteConfirm({
  title,
  busy,
  message,
  onCancel,
  onDelete,
}: {
  title: string;
  busy: boolean;
  /** Why the delete failed, or null. */
  message: string | null;
  onCancel: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="conversation-menu" role="alertdialog" aria-label="Delete conversation">
      <p>Delete “{title}” for good? Its messages and files are removed; its usage stays counted.</p>
      <div className="conversation-menu-actions">
        <button type="button" className="button button-ghost" onClick={onCancel}>
          Cancel
        </button>
        <button type="button" className="button button-danger" disabled={busy} onClick={onDelete}>
          {busy ? "Deleting…" : "Delete"}
        </button>
      </div>
      {message && <p className="form-error">{message}</p>}
    </div>
  );
}
