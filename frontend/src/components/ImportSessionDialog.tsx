import { useEffect, useState } from "react";

import { formatSize } from "../api/attachments";
import { api, errorMessage, type ConversationSummary, type Project } from "../api/client";
import type { components } from "../api/schema";
import { ReviewWarnings } from "./MarkedText";

type TargetInfo = components["schemas"]["TargetOut"];
type SessionFile = components["schemas"]["SessionFileOut"];

export const TOOL_LABELS: Record<string, string> = { claude_code: "Claude Code", codex: "Codex" };

/**
 * Import a Claude Code or Codex session: an uploaded `.jsonl`, or a file picked from a target's
 * `~/.claude/projects` or `~/.codex/sessions`. It becomes a conversation in the chosen project with the original
 * messages and tool calls as read-only blocks; nothing in it is run.
 */
export default function ImportSessionDialog({
  environmentId,
  projects,
  initialProject,
  model,
  effort,
  preset,
  onImported,
  onClose,
}: {
  environmentId: number;
  projects: Project[];
  initialProject: number | null;
  model: string;
  effort: string;
  preset: string | null;
  onImported: (summary: ConversationSummary) => void;
  onClose: () => void;
}) {
  const [projectId, setProjectId] = useState<number | null>(initialProject);
  const [source, setSource] = useState<"upload" | "target">("upload");
  const [file, setFile] = useState<File | null>(null);
  const [targets, setTargets] = useState<TargetInfo[] | null>(null);
  const [targetId, setTargetId] = useState<number | null>(null);
  const [sessions, setSessions] = useState<SessionFile[] | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // An imported session that hides characters waits here until the user has read the warning.
  const [reviewed, setReviewed] = useState<{ summary: ConversationSummary; warnings: string[] } | null>(null);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    if (source !== "target" || targets !== null) return;
    void api
      .GET("/api/v1/environments/{environment_id}/targets", { params: { path: { environment_id: environmentId } } })
      .then(({ data }) => {
        const ready = (data ?? []).filter((target) => target.state === "ready");
        setTargets(ready);
        if (ready[0]) setTargetId(ready[0].id);
      });
  }, [source, targets, environmentId]);

  async function listSessions() {
    if (targetId === null) return;
    setBusy(true);
    setError(null);
    setSessions(null);
    setPicked(null);
    const { data, error: requestError } = await api.GET("/api/v1/environments/{environment_id}/targets/{target_id}/sessions", {
      params: { path: { environment_id: environmentId, target_id: targetId } },
    });
    setBusy(false);
    if (!data) setError(errorMessage(requestError, "The sessions on this computer could not be listed."));
    else setSessions(data);
  }

  async function importNow() {
    setBusy(true);
    setError(null);
    let summary: (ConversationSummary & { warnings: string[] }) | null = null;
    if (source === "upload" && file) {
      const form = new FormData();
      form.append("file", file, file.name);
      form.append("model", model);
      form.append("effort", effort);
      if (projectId !== null) form.append("project_id", String(projectId));
      if (preset) form.append("preset", preset);
      const response = await fetch(`/api/v1/environments/${environmentId}/conversations/import`, { method: "POST", body: form });
      const body = await response.json().catch(() => null);
      if (response.ok) summary = body as ConversationSummary & { warnings: string[] };
      else setError(errorMessage(body, `The session could not be imported (HTTP ${response.status}).`));
    } else if (source === "target" && targetId !== null && picked) {
      const { data, error: requestError } = await api.POST("/api/v1/environments/{environment_id}/conversations/import-from-target", {
        params: { path: { environment_id: environmentId } },
        body: { target_id: targetId, path: picked, project_id: projectId, model, effort, preset },
      });
      if (data) summary = data;
      else setError(errorMessage(requestError, "The session could not be imported."));
    }
    setBusy(false);
    if (!summary) return;
    if (summary.warnings.length > 0) setReviewed({ summary, warnings: summary.warnings });
    else onImported(summary);
  }

  if (reviewed) {
    return (
      <div className="palette-backdrop" onMouseDown={() => onImported(reviewed.summary)}>
        <div
          className="palette import-dialog"
          role="dialog"
          aria-modal="true"
          aria-label="Session imported"
          onMouseDown={(event) => event.stopPropagation()}
        >
          <h2>Session imported</h2>
          <p className="small">The session was imported, but it hides characters from a reader. Look at it before you continue it.</p>
          <ReviewWarnings warnings={reviewed.warnings} />
          <div className="import-row import-actions">
            <button type="button" className="button button-primary" onClick={() => onImported(reviewed.summary)}>
              Open the conversation
            </button>
          </div>
        </div>
      </div>
    );
  }

  const ready = source === "upload" ? file !== null : targetId !== null && picked !== null;
  return (
    <div className="palette-backdrop" onMouseDown={onClose}>
      <div
        className="palette import-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Import a session"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <h2>Import a session</h2>
        <p className="faint small">
          A Claude Code or Codex session becomes a conversation here, with its messages and tool calls as they were. Nothing
          in it is run again; you can continue it with the model of a new conversation.
        </p>
        <label className="import-field">
          <span>Project</span>
          <select value={projectId ?? ""} onChange={(event) => setProjectId(event.target.value ? Number(event.target.value) : null)}>
            <option value="">No project</option>
            {projects.map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </select>
        </label>
        <div className="import-sources" role="radiogroup" aria-label="Where the session is">
          <label>
            <input type="radio" name="import-source" checked={source === "upload"} onChange={() => setSource("upload")} />
            Upload a file
          </label>
          <label>
            <input type="radio" name="import-source" checked={source === "target"} onChange={() => setSource("target")} />
            From a computer (target)
          </label>
        </div>
        {source === "upload" ? (
          <label className="import-field">
            <span>
              Session file: <span className="mono">~/.claude/projects/…/&lt;session&gt;.jsonl</span> or{" "}
              <span className="mono">~/.codex/sessions/…/rollout-*.jsonl</span>
            </span>
            <input type="file" accept=".jsonl,application/jsonl,application/x-ndjson" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
          </label>
        ) : targets === null ? (
          <p className="faint small">Loading the targets…</p>
        ) : targets.length === 0 ? (
          <p className="faint small">No target is ready. Add and test one in Settings › Targets.</p>
        ) : (
          <>
            <div className="import-row">
              <select aria-label="Target" value={targetId ?? ""} onChange={(event) => setTargetId(Number(event.target.value))}>
                {targets.map((target) => (
                  <option key={target.id} value={target.id}>
                    {target.name} ({target.user}@{target.host})
                  </option>
                ))}
              </select>
              <button type="button" className="button button-ghost" disabled={busy || targetId === null} onClick={() => void listSessions()}>
                {busy && sessions === null ? "Reading…" : "List sessions"}
              </button>
            </div>
            {sessions !== null &&
              (sessions.length === 0 ? (
                <p className="faint small">No Claude Code or Codex sessions in this user's home folder.</p>
              ) : (
                <ul className="import-sessions" role="listbox" aria-label="Sessions">
                  {sessions.map((item) => (
                    <li key={item.path}>
                      <label>
                        <input type="radio" name="import-session" checked={picked === item.path} onChange={() => setPicked(item.path)} />
                        <span className="chip">{TOOL_LABELS[item.tool] ?? item.tool}</span>
                        <span className="import-session-name" title={item.path}>
                          {item.folder} · {item.path.split("/").pop()}
                        </span>
                        <span className="faint small">
                          {item.modified ? new Date(item.modified * 1000).toLocaleString() : ""} · {formatSize(item.size)}
                        </span>
                      </label>
                    </li>
                  ))}
                </ul>
              ))}
          </>
        )}
        {error && <p className="form-error small">{error}</p>}
        <div className="import-row import-actions">
          <button type="button" className="button button-ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="button button-primary" disabled={busy || !ready} onClick={() => void importNow()}>
            {busy && ready ? "Importing…" : "Import"}
          </button>
        </div>
      </div>
    </div>
  );
}
