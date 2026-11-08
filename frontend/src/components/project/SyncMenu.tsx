import { useEffect, useRef, useState } from "react";

import type { GitRepository } from "../../api/client";
import { ChevronIcon } from "../icons";
import { type ChangesActions, syncLabel } from "./changesState";
import type { SyncAction } from "./useGitChanges";

const RUNNING: Record<SyncAction, string> = { fetch: "Fetching…", pull: "Pulling…", push: "Pushing…" };

/** *Sync · ↑1 · ↓0*, whose menu holds Fetch, Pull and Push. */
export default function SyncMenu({
  repository,
  actions,
  busy,
  disabled,
  onSync,
}: {
  repository: GitRepository;
  actions: ChangesActions;
  /** The sync action that runs, if any. */
  busy: SyncAction | null;
  disabled: boolean;
  onSync: (action: SyncAction) => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const outside = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  const choose = (action: SyncAction) => {
    setOpen(false);
    onSync(action);
  };

  return (
    <div className="cp-sync" ref={root}>
      <button
        type="button"
        className={`button cp-sync-button${actions.sync === "primary" ? " button-primary" : ""}`}
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen(!open)}
      >
        {busy ? RUNNING[busy] : syncLabel(repository)}
        <span className="cp-sync-caret">
          <ChevronIcon size={12} />
        </span>
      </button>
      {open && (
        <div className="conversation-menu cp-sync-menu" role="menu">
          <button type="button" role="menuitem" disabled={!actions.fetch} onClick={() => choose("fetch")}>
            Fetch
          </button>
          <button type="button" role="menuitem" disabled={!actions.pull} onClick={() => choose("pull")}>
            Pull{repository.behind ? ` (${repository.behind})` : ""}
          </button>
          <button type="button" role="menuitem" disabled={!actions.push} onClick={() => choose("push")}>
            Push{repository.ahead ? ` (${repository.ahead})` : ""}
          </button>
        </div>
      )}
    </div>
  );
}
