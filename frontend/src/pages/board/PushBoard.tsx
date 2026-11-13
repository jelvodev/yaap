import { useState } from "react";

import type { Board } from "./boardApi";
import { pushBoard } from "./boardApi";

/** The remote as text, without any user:password part; null when there is none to show. */
export function displayRemote(url: string | null): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    parsed.username = "";
    parsed.password = "";
    return parsed.toString();
  } catch {
    // Not a URL (an scp-like address): drop what comes before an "@" instead.
    return url.includes("://") ? null : url.replace(/^[^@/]*:[^@/]*@/, "");
  }
}

/** The Push board button with its confirmation: only the board's branch goes to the remote. */
export default function PushBoard({
  projectId,
  repositoryUrl,
  pushed,
  storage,
  location,
  reload,
}: {
  projectId: number;
  repositoryUrl: string | null;
  pushed: boolean;
  storage: Board["storage"];
  location: Board["location"];
  reload: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [needsAck, setNeedsAck] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [done, setDone] = useState<string | null>(null);

  function close() {
    setOpen(false);
    setNeedsAck(false);
    setAcknowledged(false);
    setMessage(null);
  }

  async function push() {
    setBusy(true);
    const answer = await pushBoard(projectId, needsAck && acknowledged);
    setBusy(false);
    if (answer.ok) {
      close();
      setDone(`Pushed ${answer.data.slice(0, 7)}`);
      await reload();
      return;
    }
    setMessage(answer.problem.message);
    setNeedsAck(answer.problem.code === "public_remote");
  }

  const separate = storage === "repository";
  const shown = separate ? (location?.replace(/^Repository /, "") ?? null) : repositoryUrl;
  const remote = displayRemote(shown) ?? "the project's remote";
  return (
    <div className="board-push">
      {done ? <span className="faint small">{done}</span> : pushed && <span className="faint small">Pushed</span>}
      <button type="button" className="button button-ghost board-small" onClick={() => setOpen(!open)}>
        Push board
      </button>
      {open && (
        <div className="board-push-panel" role="dialog" aria-label="Push board">
          {separate ? (
            <p>
              Push the board to its separate repository <span className="mono">{remote}</span>?
            </p>
          ) : (
            <>
              <p>
                Push the board's branch yaap-board to <span className="mono">{remote}</span>?
              </p>
              <p className="faint small">Only the board branch is pushed.</p>
            </>
          )}
          {message && <p className="form-error small">{message}</p>}
          {needsAck && (
            <label className="board-check">
              <input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} />
              I understand this may publish the board's cards
            </label>
          )}
          <div className="board-actions">
            <button
              type="button"
              className="button button-primary board-small"
              disabled={busy || (needsAck && !acknowledged)}
              onClick={() => void push()}
            >
              Push
            </button>
            <button type="button" className="button button-ghost board-small" onClick={close}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
