import { type MergeState, useMerge } from "./useMerge";

function CommandList({ label, commands }: { label: string; commands: string[] }) {
  if (commands.length === 0) return null;
  return (
    <div>
      <span className="plan-section-label">{label}</span>
      <ul>
        {commands.map((command) => (
          <li key={command}>
            <code className="plan-run">{command}</code>
          </li>
        ))}
      </ul>
    </div>
  );
}

function LastResult({ last }: { last: NonNullable<MergeState["last"]> }) {
  if (last.status === "merged") return last.commit ? <p className="faint">Commit {last.commit.slice(0, 12)}</p> : null;
  if (last.status === "running") return null;
  return (
    <>
      <p className="plan-failure">{last.status === "conflict" ? "The merge had a conflict." : "The merge failed."}</p>
      <pre className="card-merge-output">{last.output}</pre>
    </>
  );
}

/** A card's merge into its version: what runs, the button, and how the last merge went. */
export default function MergePart({
  projectId,
  cardId,
  onChanged,
}: {
  projectId: number;
  cardId: number;
  onChanged: () => Promise<void>;
}) {
  const { state, error, busy, merge } = useMerge(projectId, cardId, onChanged);
  if (!state) return error ? <p className="form-error small">{error}</p> : null;
  if (!state.version) return null;
  const running = state.running || state.last?.status === "running";
  return (
    <div className="card-detail-box card-merge">
      <div>
        Version {state.version}
        {state.branch && (
          <>
            {" · branch "}
            <code>{state.branch}</code>
          </>
        )}
      </div>
      {state.merged && <p>Merged into the version.</p>}
      {running && <p>Merging…</p>}
      {state.offered && !running && (
        <>
          <CommandList label="The version's checks (approved with this click):" commands={state.version_commands} />
          <CommandList label="Already approved with the cards' plans:" commands={state.card_commands} />
          <div className="card-detail-actions">
            <button type="button" className="button button-primary" disabled={busy} onClick={() => void merge()}>
              Merge into the version
            </button>
          </div>
        </>
      )}
      {!state.offered && !state.merged && !running && state.refusal && <p className="faint">{state.refusal}</p>}
      {state.last && !running && <LastResult last={state.last} />}
      {error && <p className="form-error small">{error}</p>}
    </div>
  );
}
