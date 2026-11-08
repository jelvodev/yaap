import { Link } from "react-router-dom";

import type { EvalRunDetail } from "./useEvals";

export const PLAY_LABELS: Record<string, string> = {
  queued: "Queued",
  running: "Playing",
  passed: "Passed",
  failed: "Failed",
  error: "Not judged",
  stopped: "Stopped",
};

export function money(value: number | null | undefined): string {
  return value === null || value === undefined ? "–" : `$${value.toFixed(value < 0.01 && value > -0.01 ? 4 : 2)}`;
}

/** One eval run with every play: its verdict, each check, usage and links to its conversation. */
export default function EvalRunDetailView({ run }: { run: EvalRunDetail }) {
  return (
    <ul className="pp-eval-results">
      {run.results.map((result) => (
        <li key={result.id} className="pp-eval-result">
          <div className="pp-task-head">
            <span className={`pp-run-status pp-eval-${result.status}`}>{PLAY_LABELS[result.status] ?? result.status}</span>
            <span className="mono small">
              {result.case_name}
              {run.repeats > 1 && <span className="faint"> · play {result.repeat}</span>}
            </span>
            {result.conversation_id !== null && (
              <Link className="link-button small" to={`/chat/${result.conversation_id}`}>
                Conversation
              </Link>
            )}
          </div>
          {result.status !== "queued" && result.status !== "running" && (
            <span className="faint small">
              {money(result.cost)} · {result.tokens.toLocaleString()} tokens · {result.steps} steps · {result.seconds.toFixed(0)} s
              {result.asked > 0 && ` · ${result.asked} refused as asked`}
              {result.scorer_cost > 0 && ` · scorer ${money(result.scorer_cost)}`}
            </span>
          )}
          {result.detail && <span className="pp-run-summary small">{result.detail}</span>}
          {result.checks.length > 0 && (
            <ul className="pp-eval-checks">
              {result.checks.map((check, index) => (
                <li key={index} title={check.detail}>
                  <span className={`pp-eval-${check.status}`}>{check.status === "passed" ? "✓" : check.status === "failed" ? "✗" : "?"}</span>{" "}
                  <span className="small">{check.name}</span>
                  {check.status !== "passed" && <pre className="pp-eval-detail small">{check.detail}</pre>}
                </li>
              ))}
            </ul>
          )}
          {result.workspace && (
            <span className="faint small">
              Workspace kept in <span className="mono">{result.workspace}</span>
              {result.workspace_expires_at && ` until ${new Date(result.workspace_expires_at).toLocaleString()}`}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}
