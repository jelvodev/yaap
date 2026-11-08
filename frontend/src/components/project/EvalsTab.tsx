import { useState } from "react";

import EvalComparisonView from "./EvalComparison";
import EvalRunDetailView, { money } from "./EvalRunDetail";
import { type EvalCase, type EvalRun, isOpen, useEvals } from "./useEvals";

const RUN_LABELS: Record<string, string> = {
  queued: "Queued",
  running: "Running",
  done: "Done",
  stopped: "Stopped",
  error: "Failed",
};

function CaseRow({ item, onRun }: { item: EvalCase; onRun: () => void }) {
  return (
    <li className="pp-task">
      <div className="pp-task-head">
        <span className="mono small">{item.name}</span>
        {item.valid ? (
          <span className="faint small">
            {item.start} · {item.checks} check{item.checks === 1 ? "" : "s"}
            {item.budget && ` · budget ${item.budget}`}
          </span>
        ) : (
          <span className="pp-eval-failed small">Not valid</span>
        )}
        {item.valid && (
          <button type="button" className="button button-ghost" onClick={onRun}>
            Run
          </button>
        )}
      </div>
      {item.summary && <span className="small">{item.summary}</span>}
      {[...item.problems, ...item.warnings].map((problem) => (
        <span key={problem} className={item.problems.includes(problem) ? "form-error small" : "muted small"}>
          {problem}
        </span>
      ))}
    </li>
  );
}

function RunRow({
  run,
  selected,
  onSelect,
  onStop,
  onRemove,
}: {
  run: EvalRun;
  selected: boolean;
  onSelect: () => void;
  onStop: () => void;
  onRemove: () => void;
}) {
  return (
    <div className="pp-task">
      <div className="pp-task-head">
        <button type="button" className="link-button" aria-expanded={selected} onClick={onSelect}>
          #{run.id} {run.case_name ?? "all cases"}
        </button>
        <span className={`pp-run-status pp-run-${run.status}`}>{RUN_LABELS[run.status] ?? run.status}</span>
        {isOpen(run) && <span className="running-dot" aria-label="Running" />}
        <span className="faint small">
          {run.finished}/{run.plays} played · {run.passed} passed · {money(run.cost)}
        </span>
        {isOpen(run) ? (
          <button type="button" className="button button-ghost" onClick={onStop}>
            Stop
          </button>
        ) : (
          <button type="button" className="button button-ghost" onClick={onRemove}>
            Remove
          </button>
        )}
      </div>
      <span className="faint small">
        {new Date(run.created_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })} · {run.trigger}
        {run.variant && ` · ${run.variant.label}`}
        {run.repeats > 1 && ` · ${run.repeats} plays per case`}
      </span>
      {run.summary && <span className="pp-run-summary small">{run.summary}</span>}
    </div>
  );
}

/** The project's eval cases and runs: start a run, follow it, compare two. */
export default function EvalsTab({ projectId }: { projectId: number }) {
  const evals = useEvals(projectId);
  const [view, setView] = useState<"runs" | "compare">("runs");
  const { cases, runs, detail } = evals;

  if (cases === null || runs === null) return <p className="faint small pp-pad">Loading…</p>;
  const valid = cases.filter((item) => item.valid);

  return (
    <div className="pp-pad pp-tasks">
      <p className="faint small">
        Eval cases are tasks with a known good outcome in <span className="mono">.blueprint/evals/</span>. A run plays each case in a
        throwaway copy of the project without asking anyone: calls that would ask for approval are refused and counted.
      </p>
      <div className="segmented" role="group" aria-label="Evals view">
        <button type="button" aria-pressed={view === "runs"} onClick={() => setView("runs")}>
          Cases and runs
        </button>
        <button type="button" aria-pressed={view === "compare"} onClick={() => setView("compare")}>
          Compare
        </button>
      </div>
      {evals.error && <p className="form-error small">{evals.error}</p>}
      {view === "compare" ? (
        <EvalComparisonView
          projectId={projectId}
          runs={runs}
          onStartComparison={(variant, repeats) => evals.startComparison(variant, null, repeats)}
        />
      ) : (
        <>
          <div className="pp-row">
            <button type="button" className="button button-primary" disabled={valid.length === 0} onClick={() => void evals.start(null, 1)}>
              Run all {valid.length} case{valid.length === 1 ? "" : "s"}
            </button>
            <button type="button" className="button button-ghost" onClick={() => void evals.reload()}>
              Reload
            </button>
          </div>
          {cases.length === 0 && <p className="faint small">No eval cases yet.</p>}
          <ul className="pp-task-list">
            {cases.map((item) => (
              <CaseRow key={item.name} item={item} onRun={() => void evals.start(item.name, 1)} />
            ))}
          </ul>
          <h3 className="small">Runs</h3>
          {runs.length === 0 && <p className="faint small">No runs yet.</p>}
          <ul className="pp-task-list">
            {runs.map((run) => (
              <li key={run.id} className="pp-eval-run">
                <RunRow
                  run={run}
                  selected={detail?.id === run.id}
                  onSelect={() => evals.select(detail?.id === run.id ? null : run.id)}
                  onStop={() => void evals.stop(run.id)}
                  onRemove={() => void evals.remove(run.id)}
                />
                {detail?.id === run.id && <EvalRunDetailView run={detail} />}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
