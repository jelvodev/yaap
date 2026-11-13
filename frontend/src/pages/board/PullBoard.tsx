import { useState } from "react";

import { pullBoard } from "./boardApi";

/** The Pull button of a board in a separate repository: fetches the remote's changes, or the whole board when the
 * local clone is missing. */
export default function PullBoard({ projectId, reload }: { projectId: number; reload: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ text: string; failed: boolean } | null>(null);

  async function pull() {
    setBusy(true);
    setResult(null);
    const answer = await pullBoard(projectId);
    setBusy(false);
    if (!answer.ok) {
      setResult({ text: answer.problem.message, failed: true });
      return;
    }
    setResult({ text: answer.data === null ? "Up to date" : "Pulled", failed: false });
    await reload();
    window.setTimeout(() => setResult(null), 4000);
  }

  return (
    <div className="board-push">
      {result && <span className={result.failed ? "form-error small" : "faint small"}>{result.text}</span>}
      <button type="button" className="button button-ghost board-small" disabled={busy} onClick={() => void pull()}>
        Pull
      </button>
    </div>
  );
}
