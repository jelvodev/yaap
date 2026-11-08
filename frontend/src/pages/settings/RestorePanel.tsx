import { useEffect, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";

type RestoreCheck = components["schemas"]["RestoreCheckOut"];

/** Whether YAAP answers again after the restart. */
async function yaapIsBack(): Promise<boolean> {
  try {
    const response = await fetch("/api/v1/environments", { cache: "no-store" });
    return response.ok;
  } catch {
    return false;
  }
}

/** The restart after a restore: YAAP is away for a moment, then the page loads again with the restored data. */
function RestoreProgress({ safetyBackup }: { safetyBackup: string }) {
  const [phase, setPhase] = useState<"stopping" | "starting" | "back">("stopping");

  useEffect(() => {
    let cancelled = false;
    let wentAway = false;
    const started = Date.now();
    async function poll() {
      if (cancelled) return;
      const up = await yaapIsBack();
      if (!up) {
        wentAway = true;
        setPhase("starting");
      } else if (wentAway || Date.now() - started > 20_000) {
        setPhase("back");
        window.setTimeout(() => window.location.assign("/"), 800);
        return;
      }
      window.setTimeout(() => void poll(), 1500);
    }
    window.setTimeout(() => void poll(), 1500);
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="restore-progress" role="status" aria-live="polite">
      <h3>Restoring…</h3>
      <ol>
        <li className="done">A backup of the current state was made: <span className="mono">{safetyBackup}</span></li>
        <li className={phase === "stopping" ? "active" : "done"}>YAAP stops its replies and processes</li>
        <li className={phase === "starting" ? "active" : phase === "back" ? "done" : ""}>
          YAAP starts again and puts the backup in place
        </li>
        <li className={phase === "back" ? "active" : ""}>The page loads again</li>
      </ol>
      <p className="faint small">
        In Docker YAAP starts again by itself. When it runs without Docker, start it again yourself.
      </p>
    </div>
  );
}

/** Check a backup, confirm by typing its name, then restore it. */
export default function RestorePanel({ name, onClose }: { name: string; onClose: () => void }) {
  const [check, setCheck] = useState<RestoreCheck | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [safetyBackup, setSafetyBackup] = useState<string | null>(null);

  useEffect(() => {
    void api
      .GET("/api/v1/backups/{name}/restore-check", { params: { path: { name } } })
      .then(({ data, error: failure }) => (data ? setCheck(data) : setError(errorMessage(failure))));
  }, [name]);

  async function restore() {
    setBusy(true);
    setError(null);
    const { data, error: failure } = await api.POST("/api/v1/backups/{name}/restore", {
      params: { path: { name } },
      body: { confirm: typed },
    });
    setBusy(false);
    if (!data) {
      setError(errorMessage(failure, "The restore could not start."));
      return;
    }
    setSafetyBackup(data.safety_backup);
  }

  if (safetyBackup) return <RestoreProgress safetyBackup={safetyBackup} />;
  return (
    <div className="restore-panel">
      <h3>
        Restore <span className="mono">{name}</span>
      </h3>
      {!check && !error && <p className="faint small">Checking the backup…</p>}
      {check && (
        <>
          <p className="small">
            Made {check.created_at ? new Date(check.created_at).toLocaleString() : "at an unknown time"}
            {Object.keys(check.files).length > 0 &&
              ` · ${Object.entries(check.files)
                .map(([folder, count]) => `${folder}: ${count} files`)
                .join(", ")}`}
          </p>
          {check.problems.length > 0 ? (
            <ul className="form-error small">
              {check.problems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          ) : (
            <>
              <p className="small">
                The database, the Blueprint, the attachments, the knowledge documents and the project folders are
                replaced by the backup. Everything made since then is gone from YAAP, but first a fresh backup of the
                current state is made, and the replaced data stays in <span className="mono">data/.restore-previous</span>{" "}
                until the next restore. Running replies and processes stop; YAAP restarts.
              </p>
              <label className="small" htmlFor="restore-confirm">
                Type <span className="mono">{name}</span> to confirm
              </label>
              <input
                id="restore-confirm"
                className="mono"
                autoComplete="off"
                value={typed}
                onChange={(event) => setTyped(event.target.value)}
              />
            </>
          )}
        </>
      )}
      {error && <p className="form-error small">{error}</p>}
      <div className="form-row">
        {check?.restorable && (
          <button type="button" className="button button-danger" disabled={busy || typed.trim() !== name} onClick={() => void restore()}>
            {busy ? "Preparing…" : "Restore and restart"}
          </button>
        )}
        <button type="button" className="button button-ghost" onClick={onClose}>
          Cancel
        </button>
      </div>
    </div>
  );
}
