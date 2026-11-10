import { useEffect } from "react";

import type { Project } from "../../api/client";
import "./confirm-dialog.css";
import { useMoveTarget } from "./useMoveTarget";

/**
 * Asks where a conversation goes: another project of its environment, or no project. It says what stays behind,
 * since the files and checkpoints belong to the old project.
 */
export default function MoveConversationDialog({
  title,
  currentProjectId,
  projects,
  onMove,
  onClose,
}: {
  title: string;
  currentProjectId: number | null;
  projects: Project[];
  /** Resolves to the error to show, or null when the conversation moved. */
  onMove: (projectId: number | null) => Promise<string | null>;
  onClose: () => void;
}) {
  const { target, choose, busy, error, submit } = useMoveTarget(onMove, onClose);
  const places: { id: number | null; name: string }[] = [
    { id: null, name: "No project" },
    ...projects.map((project) => ({ id: project.id, name: project.name })),
  ].filter((place) => place.id !== currentProjectId);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="palette-backdrop confirm-backdrop" onMouseDown={onClose}>
      <div
        className="palette confirm-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="move-dialog-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <h2 id="move-dialog-title">Move “{title}” to a project</h2>
        <p className="confirm-dialog-message">
          Files the conversation wrote and its checkpoints stay with the old project, and cannot be restored from
          here afterwards. Processes and shell sessions of the conversation stop. A conversation that is replying
          cannot move.
        </p>
        <fieldset className="move-places" disabled={busy}>
          <legend className="visually-hidden">Move to</legend>
          {places.map((place) => (
            <label key={place.id ?? "none"}>
              <input
                type="radio"
                name="move-target"
                checked={target === place.id}
                onChange={() => choose(place.id)}
              />{" "}
              {place.name}
            </label>
          ))}
        </fieldset>
        {error && <p className="form-error">{error}</p>}
        <div className="confirm-dialog-actions">
          <button type="button" className="button button-ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="button button-primary"
            disabled={busy || target === undefined}
            onClick={() => void submit()}
          >
            {busy ? "Moving…" : "Move"}
          </button>
        </div>
      </div>
    </div>
  );
}
