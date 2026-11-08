import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api, errorMessage } from "../api/client";
import type { components } from "../api/schema";

type Process = components["schemas"]["ProcessOut"];
type ShellSession = components["schemas"]["ShellSessionOut"];

function since(value: string): string {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  return hours < 48 ? `${hours} h ago` : new Date(value).toLocaleDateString();
}

function stateText(process: Process): string {
  if (process.status === "running") return "Running";
  const code = process.exit_code !== null ? ` (exit code ${process.exit_code})` : "";
  const why = process.ended_reason ? `: ${process.ended_reason}` : "";
  return `${process.status === "exited" ? "Ended by itself" : "Stopped"}${code}${why}`;
}

function until(value: string): string {
  const minutes = Math.max(0, Math.round((new Date(value).getTime() - Date.now()) / 60000));
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

/** Background processes the agent started in this project: running ones with Stop, then recent ones;
 * and the open shell sessions of its conversations with End session. */
export default function ProcessesSection({ projectId }: { projectId: number }) {
  const [processes, setProcesses] = useState<Process[]>([]);
  const [sessions, setSessions] = useState<ShellSession[]>([]);
  const [ending, setEnding] = useState<number | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [stopping, setStopping] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const { data } = await api.GET("/api/v1/projects/{project_id}/processes", {
      params: { path: { project_id: projectId } },
    });
    setProcesses(data ?? []);
    const { data: open } = await api.GET("/api/v1/projects/{project_id}/shell-sessions", {
      params: { path: { project_id: projectId } },
    });
    setSessions(open ?? []);
  }, [projectId]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [load]);

  async function stop(process: Process) {
    setStopping(process.id);
    setError(null);
    const { error: failure } = await api.POST("/api/v1/projects/{project_id}/processes/{process_id}/stop", {
      params: { path: { project_id: projectId, process_id: process.id } },
    });
    setStopping(null);
    if (failure) setError(errorMessage(failure, "The process could not be stopped."));
    await load();
  }

  async function end(shell: ShellSession) {
    setEnding(shell.id);
    setError(null);
    const { error: failure } = await api.DELETE("/api/v1/projects/{project_id}/shell-sessions/{session_id}", {
      params: { path: { project_id: projectId, session_id: shell.id } },
    });
    setEnding(null);
    if (failure) setError(errorMessage(failure, "The session could not be ended."));
    await load();
  }

  const running = processes.filter((process) => process.status === "running");
  return (
    <section className="settings-section">
      <h2>Processes</h2>
      <p className="muted small">
        Servers and watchers the agent started with start_process. They keep running after the reply and stop after an
        hour without activity in their conversation, or here.
      </p>
      {processes.length === 0 ? (
        <p className="faint small">No processes yet.</p>
      ) : (
        <div className="card">
          <p className="small">
            {running.length} running{processes.length > running.length ? ` · ${processes.length - running.length} ended` : ""}
          </p>
          <ul className="process-list">
            {processes.map((process) => (
              <li key={process.id} className={process.status === "running" ? "process-running" : "process-ended"}>
                <div className="process-head">
                  <span className={`process-dot${process.status === "running" ? " live" : ""}`} aria-hidden="true" />
                  <code className="process-command" title={process.command}>
                    {process.command}
                  </code>
                  {process.port && <span className="mono small">port {process.port}</span>}
                  {process.status === "running" && (
                    <button
                      type="button"
                      className="button button-danger"
                      disabled={stopping !== null}
                      onClick={() => void stop(process)}
                    >
                      {stopping === process.id ? "Stopping…" : "Stop"}
                    </button>
                  )}
                </div>
                <p className="faint small">
                  {stateText(process)} · started {since(process.started_at)}
                  {process.conversation_id !== null && (
                    <>
                      {" · "}
                      <Link to={`/chat/${process.conversation_id}`}>conversation</Link>
                    </>
                  )}
                  {" · "}
                  <button type="button" className="link-button" onClick={() => setOpen(open === process.id ? null : process.id)}>
                    {open === process.id ? "Hide output" : "Show output"}
                  </button>
                </p>
                {open === process.id && <pre className="import-preview">{process.output_tail || "(no output)"}</pre>}
              </li>
            ))}
          </ul>
          {error && <p className="form-error">{error}</p>}
        </div>
      )}
      <h3>Shell sessions</h3>
      <p className="muted small">
        Sessions of run_command keep their folder and exported variables between replies of a conversation. They end
        after two hours without use, when the conversation is deleted, or here.
      </p>
      {sessions.length === 0 ? (
        <p className="faint small">No open sessions.</p>
      ) : (
        <div className="card">
          <ul className="process-list">
            {sessions.map((shell) => (
              <li key={shell.id} className="process-running">
                <div className="process-head">
                  <span className="process-dot live" aria-hidden="true" />
                  <code className="process-command">{shell.name}</code>
                  <span className="small">{shell.place}</span>
                  <button
                    type="button"
                    className="button button-danger"
                    disabled={ending !== null}
                    onClick={() => void end(shell)}
                  >
                    {ending === shell.id ? "Ending…" : "End session"}
                  </button>
                </div>
                <p className="faint small">
                  Used {since(shell.last_used_at)} · ends in {until(shell.ends_at)} without use ·{" "}
                  <Link to={`/chat/${shell.conversation_id}`}>{shell.conversation_title || "conversation"}</Link>
                </p>
              </li>
            ))}
          </ul>
          {error && processes.length === 0 && <p className="form-error">{error}</p>}
        </div>
      )}
    </section>
  );
}
