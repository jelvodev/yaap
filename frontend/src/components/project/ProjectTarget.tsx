import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { type Setting, api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";

type Target = components["schemas"]["TargetOut"];
type Sandbox = components["schemas"]["SandboxOut"];

interface TargetValue {
  id?: number;
  folder?: string;
  sandbox?: boolean;
  network?: boolean;
}

/** The select's value for the project's own sandbox; targets use their id. */
const SANDBOX = "sandbox";

/** Where the project's conversations work: YAAP's container, its own sandbox, or a folder on a
 * computer over SSH. */
export default function ProjectTarget({ projectId, environmentId }: { projectId: number; environmentId: number }) {
  const [targets, setTargets] = useState<Target[]>([]);
  const [sandbox, setSandbox] = useState<Sandbox | null>(null);
  const [stored, setStored] = useState<TargetValue>({});
  const [choice, setChoice] = useState<string>("");
  const [folder, setFolder] = useState("");
  const [network, setNetwork] = useState(false);
  const [message, setMessage] = useState<{ error: boolean; text: string } | null>(null);
  const url = `/api/v1/projects/${projectId}/settings/target`;

  const load = useCallback(async () => {
    const [{ data }, list, sandboxResult] = await Promise.all([
      api.GET("/api/v1/environments/{environment_id}/targets", { params: { path: { environment_id: environmentId } } }),
      fetch(`/api/v1/projects/${projectId}/settings`).then((response) => response.json() as Promise<Setting[]>),
      api.GET("/api/v1/projects/{project_id}/sandbox", { params: { path: { project_id: projectId } } }),
    ]);
    setTargets(data ?? []);
    setSandbox(sandboxResult.data ?? null);
    const value = (list.find((item) => item.key === "target")?.value ?? {}) as TargetValue;
    setStored(value);
    setChoice(value.sandbox ? SANDBOX : value.id ? String(value.id) : "");
    setFolder(value.folder ?? "");
    setNetwork(Boolean(value.network));
  }, [environmentId, projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function save() {
    if (choice === SANDBOX) {
      const { error } = await api.PUT("/api/v1/projects/{project_id}/sandbox", {
        params: { path: { project_id: projectId } },
        body: { network },
      });
      if (error) {
        setMessage({ error: true, text: errorMessage(error) });
        return;
      }
      setMessage({ error: false, text: "Saved. New conversations work in the sandbox; it starts on first use." });
      await load();
      return;
    }
    const value = choice === "" ? {} : { id: Number(choice), folder: folder.trim() };
    const response = await fetch(url, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value }),
    });
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      setMessage({ error: true, text: errorMessage(body, `Saving failed (HTTP ${response.status}).`) });
      return;
    }
    setMessage({ error: false, text: "Saved. New conversations work there." });
    await load();
  }

  const storedChoice = stored.sandbox ? SANDBOX : stored.id ? String(stored.id) : "";
  const onTarget = choice !== "" && choice !== SANDBOX;
  const chosen = onTarget ? targets.find((target) => String(target.id) === choice) : undefined;
  const changed =
    storedChoice !== choice ||
    (onTarget && (stored.folder ?? "") !== folder.trim()) ||
    (choice === SANDBOX && Boolean(stored.network) !== network);
  const sandboxOff = sandbox !== null && !sandbox.available;
  return (
    <div className="pp-language">
      <p className="faint small">
        YAAP&apos;s container, this project&apos;s own sandbox container, or a folder on a computer from{" "}
        <Link to={`/settings/targets?environment=${environmentId}`}>Settings › Targets</Link>. On a computer every change
        asks first and cannot be rolled back in YAAP; git tools and checkpoints work only in the container and the
        sandbox. Conversations started after a change use it.
      </p>
      <form
        className="pp-row"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <select
          aria-label="Where the agent works"
          value={choice}
          onChange={(event) => {
            setChoice(event.target.value);
            const target = targets.find((item) => String(item.id) === event.target.value);
            if (target && !folder) setFolder(target.folders[0] ?? "");
          }}
        >
          <option value="">YAAP&apos;s container</option>
          <option value={SANDBOX} disabled={sandboxOff && storedChoice !== SANDBOX}>
            Sandbox{sandboxOff ? " (not enabled)" : ""}
          </option>
          {targets.map((target) => (
            <option key={target.id} value={target.id}>
              {target.name}
              {target.state !== "ready" ? " (not ready)" : ""}
            </option>
          ))}
        </select>
        {onTarget && (
          <input
            aria-label="The project's folder on that computer"
            className="mono"
            value={folder}
            placeholder={chosen?.folders[0] ?? "C:\\Users\\you\\Projects\\site"}
            onChange={(event) => setFolder(event.target.value)}
          />
        )}
        <button type="submit" className="button" disabled={!changed || (onTarget && !folder.trim())}>
          Save
        </button>
      </form>
      {choice === SANDBOX && (
        <label className="checkbox-row small">
          <input type="checkbox" checked={network} onChange={(event) => setNetwork(event.target.checked)} />
          Network access (to install packages and fetch dependencies). Changing it makes the sandbox again on its next
          use; the project folder stays.
        </label>
      )}
      {sandboxOff && (choice === SANDBOX || storedChoice === SANDBOX) && (
        <p className="small form-error">{sandbox?.detail}</p>
      )}
      {chosen && chosen.folders.length > 0 && (
        <p className="faint small">
          Allowed folders there: <span className="mono">{chosen.folders.join(", ")}</span>
          {chosen.whole_computer ? " (whole computer is on)" : ""}
        </p>
      )}
      {message && <p className={`small${message.error ? " form-error" : ""}`}>{message.text}</p>}
    </div>
  );
}
