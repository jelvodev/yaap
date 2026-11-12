import { useState } from "react";

import type { BoardProblem } from "./boardApi";

/** The storage choice before a board starts; the branch is the only storage for now. */
export default function NotStarted({
  start,
  onStarted,
  onCancel,
}: {
  start: (createRepository: boolean) => Promise<BoardProblem | null>;
  onStarted: () => void;
  onCancel?: () => void;
}) {
  const [problem, setProblem] = useState<BoardProblem | null>(null);
  const [createRepository, setCreateRepository] = useState(false);
  const [busy, setBusy] = useState(false);

  async function begin() {
    setBusy(true);
    const answer = await start(createRepository);
    setBusy(false);
    setProblem(answer);
    if (answer === null) onStarted();
  }

  return (
    <div className="board-start">
      <h3>Where the cards are kept</h3>
      <label className="board-start-choice">
        <input type="radio" name="board-storage" checked readOnly />
        <span>
          On the branch yaap-board of this project's repository
          <span className="faint small board-start-note">
            Plain Markdown files on their own branch; your working tree and other branches are not touched.
          </span>
        </span>
      </label>
      {problem && <p className="form-error small">{problem.message}</p>}
      {problem?.code === "no_repository" && (
        <label className="board-check">
          <input type="checkbox" checked={createRepository} onChange={(e) => setCreateRepository(e.target.checked)} />
          Make an empty git repository in the project folder
        </label>
      )}
      <div className="board-actions">
        <button type="button" className="button button-primary" disabled={busy} onClick={() => void begin()}>
          Start the board
        </button>
        {onCancel && (
          <button type="button" className="button button-ghost" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </div>
  );
}
