import { useRemoteCheck } from "./useRemoteCheck";

/** The URL of the separate private repository, with a check of whether it can be reached and whether it is private. */
export default function RepositoryChoice({
  projectId,
  url,
  setUrl,
}: {
  projectId: number;
  url: string;
  setUrl: (url: string) => void;
}) {
  const remote = useRemoteCheck(projectId);
  const trimmed = url.trim();
  return (
    <div className="board-start-repository">
      <input
        type="text"
        value={url}
        placeholder="https://github.com/owner/project-board.git"
        aria-label="Repository URL"
        onChange={(e) => {
          setUrl(e.target.value);
          remote.reset();
        }}
      />
      <button
        type="button"
        className="button button-ghost board-small"
        disabled={remote.busy || trimmed === ""}
        onClick={() => void remote.check(trimmed)}
      >
        Check access
      </button>
      {remote.view && (
        <p className={remote.view.tone === "good" ? "faint small" : "form-error small"}>{remote.view.text}</p>
      )}
    </div>
  );
}
