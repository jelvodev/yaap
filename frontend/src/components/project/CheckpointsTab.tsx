import { useCallback, useEffect, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";
import DiffView from "../DiffView";

type Checkpoint = components["schemas"]["CheckpointOut"];
type CheckpointDiff = components["schemas"]["CheckpointDiffOut"];

export interface CheckpointFocus {
  id: number;
  view: "changes" | "restore";
}

function timeText(value: string): string {
  const date = new Date(value);
  const today = new Date().toDateString() === date.toDateString();
  return today
    ? date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : date.toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

function CheckpointDetail({
  projectId,
  checkpoint,
  initialView,
  onRestored,
}: {
  projectId: number;
  checkpoint: Checkpoint;
  initialView: "changes" | "restore";
  onRestored: (beforeId: number) => void;
}) {
  const [view, setView] = useState(initialView);
  const [diff, setDiff] = useState<CheckpointDiff | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const path = { params: { path: { project_id: projectId, checkpoint_id: checkpoint.id } } };

  useEffect(() => {
    let current = true;
    setDiff(null);
    setError(null);
    const request =
      view === "changes"
        ? api.GET("/api/v1/projects/{project_id}/checkpoints/{checkpoint_id}/changes", path)
        : api.GET("/api/v1/projects/{project_id}/checkpoints/{checkpoint_id}/restore-preview", path);
    void request.then(({ data, error: failure }) => {
      if (!current) return;
      if (failure || !data) setError(errorMessage(failure));
      else setDiff(data);
    });
    return () => {
      current = false;
    };
  }, [checkpoint.id, view]);

  async function restore() {
    setBusy(true);
    setError(null);
    const { data, error: failure } = await api.POST("/api/v1/projects/{project_id}/checkpoints/{checkpoint_id}/restore", {
      ...path,
      params: { ...path.params, query: { conversation_id: checkpoint.conversation_id ?? undefined } },
    });
    setBusy(false);
    if (failure || !data) {
      setError(errorMessage(failure, "The roll back failed."));
      return;
    }
    onRestored(data.before_id);
  }

  return (
    <div className="pp-checkpoint-detail">
      <div className="segmented" role="group" aria-label="Checkpoint view">
        <button type="button" aria-pressed={view === "changes"} onClick={() => setView("changes")}>
          Changes of this action
        </button>
        <button type="button" aria-pressed={view === "restore"} onClick={() => setView("restore")}>
          Restore to before this
        </button>
      </div>
      {error && <p className="form-error small">{error}</p>}
      {diff === null && !error && <p className="faint small">Loading…</p>}
      {diff !== null && view === "changes" && (
        <>
          {!diff.diff.trim() ? <p className="faint small">This action changed no files.</p> : <DiffView diff={diff.diff} />}
          {diff.truncated && <p className="faint small">The diff is cut off; it is very large.</p>}
        </>
      )}
      {diff !== null && view === "restore" && (
        <>
          {diff.paths.length === 0 ? (
            <p className="faint small">The folder is already as it was before this action.</p>
          ) : (
            <>
              <p className="small">
                Restoring puts {diff.paths.length} file{diff.paths.length === 1 ? "" : "s"} back as they were before this action;
                later changes go too. YAAP first saves the current state as a checkpoint, so you can undo this. Only files are
                restored: what commands did elsewhere, such as a database change or a push, stays.
              </p>
              <DiffView diff={diff.diff} />
              <div className="pp-row">
                <button type="button" className="button button-primary" disabled={busy} onClick={() => void restore()}>
                  {busy ? "Restoring…" : `Restore ${diff.paths.length} file${diff.paths.length === 1 ? "" : "s"}`}
                </button>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}

/** The project's checkpoints: the folder before each agent action, with its changes and a roll back. */
export default function CheckpointsTab({
  projectId,
  focus,
  refreshKey,
  onRestored,
}: {
  projectId: number;
  focus: CheckpointFocus | null;
  refreshKey: unknown;
  onRestored: () => void;
}) {
  const [checkpoints, setCheckpoints] = useState<Checkpoint[] | null>(null);
  const [selected, setSelected] = useState<CheckpointFocus | null>(focus);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    const { data } = await api.GET("/api/v1/projects/{project_id}/checkpoints", { params: { path: { project_id: projectId } } });
    setCheckpoints(data ?? []);
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  useEffect(() => {
    if (focus) setSelected(focus);
  }, [focus?.id, focus?.view]);

  if (checkpoints === null) return <p className="faint small pp-pad">Loading…</p>;
  if (checkpoints.length === 0) {
    return (
      <p className="faint small pp-pad">
        No checkpoints yet. Before each action of the agent that can change files, YAAP saves the project folder here, so you
        can see what the action changed and go back to before it.
      </p>
    );
  }

  return (
    <div className="pp-checkpoints">
      {notice && <p className="muted small pp-pad">{notice}</p>}
      <ul className="pp-checkpoint-list">
        {checkpoints.map((checkpoint, index) => {
          const groupStart = index === 0 || checkpoints[index - 1].conversation_id !== checkpoint.conversation_id;
          const isSelected = selected?.id === checkpoint.id;
          return (
            <li key={checkpoint.id}>
              {groupStart && (
                <div className="pp-checkpoint-group faint small">{checkpoint.conversation_title ?? "Outside a conversation"}</div>
              )}
              <button
                type="button"
                className="pp-checkpoint"
                aria-current={isSelected}
                onClick={() => setSelected(isSelected ? null : { id: checkpoint.id, view: "changes" })}
              >
                <span className="faint small">{timeText(checkpoint.created_at)}</span>
                <span className="pp-checkpoint-summary mono">
                  {checkpoint.kind === "restore" ? "Before a roll back" : checkpoint.summary}
                </span>
              </button>
              {isSelected && selected && (
                <CheckpointDetail
                  key={`${checkpoint.id}-${selected.view}`}
                  projectId={projectId}
                  checkpoint={checkpoint}
                  initialView={selected.view}
                  onRestored={(beforeId) => {
                    setSelected(null);
                    setNotice(`Restored. The state before is now the newest checkpoint (“Before a roll back”, #${beforeId}); restore that one to undo this.`);
                    onRestored();
                    void load();
                  }}
                />
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
