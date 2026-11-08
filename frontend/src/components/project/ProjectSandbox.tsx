import { useCallback, useEffect, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";

type Sandbox = components["schemas"]["SandboxOut"];

const STATES: Record<string, string> = {
  missing: "Not created yet; it starts on the first command",
  created: "Created",
  running: "Running",
  exited: "Stopped; it starts again on the next command",
  restarting: "Restarting",
  paused: "Paused",
  dead: "Broken; rebuild it",
};

function bytes(value: number | null | undefined): string {
  if (value === null || value === undefined) return "–";
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1)} GB`;
  return `${Math.round(value / 1024 ** 2)} MB`;
}

function time(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
}

/** The project's sandbox container: its state, CPU and memory, with Stop and Rebuild. */
export default function ProjectSandbox({ projectId }: { projectId: number }) {
  const [sandbox, setSandbox] = useState<Sandbox | null>(null);
  const [busy, setBusy] = useState<"stop" | "rebuild" | null>(null);
  const [confirmRebuild, setConfirmRebuild] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const path = { params: { path: { project_id: projectId } } };

  const load = useCallback(async () => {
    const { data } = await api.GET("/api/v1/projects/{project_id}/sandbox", { params: { path: { project_id: projectId } } });
    setSandbox(data ?? null);
  }, [projectId]);

  useEffect(() => {
    void load();
    // CPU and memory change while the agent works; read them again now and then.
    const timer = window.setInterval(() => void load(), 15000);
    return () => window.clearInterval(timer);
  }, [load]);

  async function act(kind: "stop" | "rebuild") {
    setBusy(kind);
    setError(null);
    const { data, error: failed } =
      kind === "stop"
        ? await api.POST("/api/v1/projects/{project_id}/sandbox/stop", path)
        : await api.POST("/api/v1/projects/{project_id}/sandbox/rebuild", path);
    setBusy(null);
    setConfirmRebuild(false);
    if (failed || !data) {
      setError(errorMessage(failed, kind === "stop" ? "The sandbox could not be stopped." : "The sandbox could not be rebuilt."));
      return;
    }
    setSandbox(data);
  }

  if (sandbox === null || !sandbox.available || (!sandbox.chosen && sandbox.state === "missing")) return null;
  const running = sandbox.state === "running";
  return (
    <div className="pp-sandbox">
      <p className="small">
        <strong>Sandbox:</strong> {STATES[sandbox.state] ?? sandbox.state}
        {running && sandbox.started_at ? ` since ${time(sandbox.started_at)}` : ""}
        {sandbox.network ? " · network on" : " · no network"}
      </p>
      {running && (
        <p className="faint small">
          CPU {sandbox.cpu_percent ?? "–"}% · memory {bytes(sandbox.memory_bytes)}
          {sandbox.memory_limit ? ` of ${bytes(sandbox.memory_limit)}` : ""}
          {sandbox.stops_at ? ` · stops at ${time(sandbox.stops_at)} unless used` : ""}
        </p>
      )}
      <div className="pp-row">
        <button type="button" className="button" disabled={!running || busy !== null} onClick={() => void act("stop")}>
          {busy === "stop" ? "Stopping…" : "Stop"}
        </button>
        {confirmRebuild ? (
          <>
            <button type="button" className="button button-danger" disabled={busy !== null} onClick={() => void act("rebuild")}>
              {busy === "rebuild" ? "Rebuilding…" : "Rebuild now"}
            </button>
            <button type="button" className="button button-ghost" disabled={busy !== null} onClick={() => setConfirmRebuild(false)}>
              Cancel
            </button>
          </>
        ) : (
          <button type="button" className="button" disabled={busy !== null} onClick={() => setConfirmRebuild(true)}>
            Rebuild…
          </button>
        )}
      </div>
      {confirmRebuild && (
        <p className="faint small">
          Rebuild makes a new sandbox from the current image: what was installed outside the project folder is gone; the
          project folder stays as it is.
        </p>
      )}
      {error && <p className="small form-error">{error}</p>}
    </div>
  );
}
