import { useCallback, useRef, useState } from "react";
import { NavLink } from "react-router-dom";

import type { components } from "../api/schema";
import ConversationDeleteConfirm from "./ConversationDeleteConfirm";
import { MoreIcon } from "./icons";
import { useDismiss } from "./useDismiss";

type ConversationSummary = components["schemas"]["ConversationSummaryOut"];

/** One conversation in the sidebar, with a ⋮ menu to rename, archive or restore, and delete it. */
export default function ConversationRow({
  item,
  running,
  onRename,
  onArchive,
  onDelete,
}: {
  item: ConversationSummary;
  running: boolean;
  onRename: (title: string) => Promise<string | null>;
  onArchive: (archived: boolean) => Promise<string | null>;
  onDelete: () => Promise<string | null>;
}) {
  const [mode, setMode] = useState<"closed" | "menu" | "rename" | "delete">("closed");
  const [title, setTitle] = useState(item.title);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const rowRef = useRef<HTMLLIElement | null>(null);
  const archived = Boolean(item.archived_at);

  // A click elsewhere or Escape closes the menu; renaming ends the same way without saving.
  const close = useCallback(() => {
    setMode("closed");
    setMessage(null);
  }, []);
  useDismiss(rowRef, mode !== "closed", close);

  async function run(action: () => Promise<string | null>) {
    setBusy(true);
    const problem = await action();
    setBusy(false);
    setMessage(problem);
    if (!problem) setMode("closed");
  }

  return (
    <li ref={rowRef} className={mode === "closed" ? "conversation-row" : "conversation-row menu-open"}>
      {mode === "rename" ? (
        <form
          className="conversation-rename"
          onSubmit={(event) => {
            event.preventDefault();
            if (title.trim() === item.title) setMode("closed");
            else void run(() => onRename(title.trim()));
          }}
        >
          <label className="visually-hidden" htmlFor={`rename-${item.id}`}>
            Conversation name
          </label>
          <input
            id={`rename-${item.id}`}
            value={title}
            maxLength={200}
            autoFocus
            disabled={busy}
            onFocus={(event) => event.target.select()}
            onChange={(event) => setTitle(event.target.value)}
          />
        </form>
      ) : (
        <NavLink to={`/chat/${item.id}`} title={item.title}>
          {running && <span className="running-dot" aria-label="Replying" />}
          {item.scheduled && (
            <span className="conversation-clock" aria-label="Made by a scheduled task" title="Made by a scheduled task">
              ◷
            </span>
          )}
          {item.imported && (
            <span
              className="conversation-clock"
              aria-label="Imported session"
              title={`Imported from a ${item.imported.tool === "codex" ? "Codex" : "Claude Code"} session`}
            >
              ⇲
            </span>
          )}
          <span className={item.unread ? "conversation-unread" : undefined}>{item.title}</span>
        </NavLink>
      )}
      {mode !== "rename" && (
        <button
          type="button"
          className="conversation-more"
          aria-label={`${item.title}: more actions`}
          aria-haspopup="menu"
          aria-expanded={mode !== "closed"}
          onClick={() => setMode(mode === "closed" ? "menu" : "closed")}
        >
          <MoreIcon size={15} />
        </button>
      )}
      {mode === "menu" && (
        <div className="conversation-menu" role="menu">
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setTitle(item.title);
              setMode("rename");
            }}
          >
            Rename
          </button>
          <button type="button" role="menuitem" disabled={busy} onClick={() => void run(() => onArchive(!archived))}>
            {archived ? "Restore from archive" : "Archive"}
          </button>
          <button type="button" role="menuitem" className="danger" onClick={() => setMode("delete")}>
            Delete
          </button>
          {message && <p className="form-error">{message}</p>}
        </div>
      )}
      {mode === "delete" && (
        <ConversationDeleteConfirm
          title={item.title}
          busy={busy}
          message={message}
          onCancel={() => setMode("menu")}
          onDelete={() => void run(onDelete)}
        />
      )}
      {mode === "rename" && message && <p className="form-error">{message}</p>}
    </li>
  );
}
