import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";
import { useScopedSettings } from "../../pages/settings/useScopedSettings";

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
  const { find, set, reload } = useScopedSettings({ kind: "project", id: projectId });
  const [choice, setChoice] = useState<string>("");
  const [folder, setFolder] = useState("");
  const [network, setNetwork] = useState(false);
  const [message, setMessage] = useState<{ error: boolean; text: string } | null>(null);
  // Bumped after a save so the form re-reads the stored value even when it did not change.
  const [syncCount, setSyncCount] = useState(0);
  const storedJson = JSON.stringify(find("target")?.value ?? {});
  const stored = useMemo(() => JSON.parse(storedJson) as TargetValue, [storedJson]);

  const loadChoices = useCallback(async () => {
    const [{ data }, sandboxResult] = await Promise.all([
      api.GET("/api/v1/environments/{environment_id}/targets", { params: { path: { environment_id: environmentId } } }),
      api.GET("/api/v1/projects/{project_id}/sandbox", { params: { path: { project_id: projectId } } }),
    ]);
    setTargets(data ?? []);
    setSandbox(sandboxResult.data ?? null);
  }, [environmentId, projectId]);

  useEffect(() => {
    void loadChoices();
  }, [loadChoices]);

  useEffect(() => {
    setChoice(stored.sandbox ? SANDBOX : stored.id ? String(stored.id) : "");
    setFolder(stored.folder ?? "");
    setNetwork(Boolean(stored.network));
  }, [stored, syncCount]);

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
      await Promise.all([reload(), loadChoices()]);
      setSyncCount((count) => count + 1);
      return;
    }
    const value = choice === "" ? {} : { id: Number(choice), folder: folder.trim() };
    const error = await set("target", value);
    if (error) {
      setMessage({ error: true, text: error });
      return;
    }
    setMessage({ error: false, text: "Saved. New conversations work there." });
    await loadChoices();
    setSyncCount((count) => count + 1);
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
