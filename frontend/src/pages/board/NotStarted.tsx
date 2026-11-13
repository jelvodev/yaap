import { useState } from "react";

import type { BoardProblem, BoardStartChoice } from "./boardApi";
import RepositoryChoice from "./RepositoryChoice";

/** The storage choice before a board starts: the project's branch or a separate private repository. */
export default function NotStarted({
  projectId,
  start,
  onStarted,
  onCancel,
  pendingTitle = null,
}: {
  projectId: number;
  /** The card that waits for the board to start. */
  pendingTitle?: string | null;
  start: (choice: BoardStartChoice) => Promise<BoardProblem | null>;
  onStarted: () => void;
  onCancel?: () => void;
}) {
  const [problem, setProblem] = useState<BoardProblem | null>(null);
  const [createRepository, setCreateRepository] = useState(false);
  const [busy, setBusy] = useState(false);
  const [storage, setStorage] = useState<"branch" | "repository">("branch");
  const [url, setUrl] = useState("");

  async function begin() {
    setBusy(true);
    const answer = await start(
      storage === "repository"
        ? { storage, create_repository: false, repository_url: url.trim() }
        : { storage, create_repository: createRepository },
    );
    setBusy(false);
    setProblem(answer);
    if (answer === null) onStarted();
  }

  return (
    <div className="board-start">
      <h3>Where the board is kept</h3>
      {pendingTitle !== null && (
        <p className="faint small board-start-note">"{pendingTitle}" is added once the board starts.</p>
      )}
      <label className="board-start-choice">
        <input type="radio" name="board-storage" checked={storage === "branch"} onChange={() => setStorage("branch")} />
        <span>
          On the branch yaap-board of this project's repository
          <span className="faint small board-start-note">
            Plain Markdown files on their own branch; your working tree and other branches are not touched.
          </span>
        </span>
      </label>
      <label className="board-start-choice">
        <input
          type="radio"
          name="board-storage"
          checked={storage === "repository"}
          onChange={() => setStorage("repository")}
        />
        <span>In a separate private repository</span>
      </label>
      <p className="faint small board-start-note">
        A pushed branch of a public repository is public too; a separate private repository keeps ideas and plans apart.
      </p>
      {storage === "repository" && <RepositoryChoice projectId={projectId} url={url} setUrl={setUrl} />}
      {problem && <p className="form-error small">{problem.message}</p>}
      {storage === "branch" && problem?.code === "no_repository" && (
        <label className="board-check">
          <input type="checkbox" checked={createRepository} onChange={(e) => setCreateRepository(e.target.checked)} />
          Make an empty git repository in the project folder
        </label>
      )}
      <div className="board-actions">
        <button
          type="button"
          className="button button-primary"
          disabled={busy || (storage === "repository" && url.trim() === "")}
          onClick={() => void begin()}
        >
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
