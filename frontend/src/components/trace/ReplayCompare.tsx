import { blueprintLine } from "./replayBlueprint";
import { REPLAY_STATUS_LABELS } from "./runLabels";
import TraceTimeline from "./TraceTimeline";
import { formatCost, type TraceRun } from "./traceOrder";
import { type ReplayComparison, replayOpen, useReplayComparison } from "./useReplays";
import { useTrace } from "./useTrace";

interface ReplayCompareProps {
  conversationId: number;
  replayId: number;
  original: TraceRun;
  onBack: () => void;
}

type Side = ReplayComparison["original"];

const ignore = () => undefined;

/** A replay beside its original: steps, tokens, cost and final answer of each, and both timelines. */
export default function ReplayCompare({ conversationId, replayId, original, onBack }: ReplayCompareProps) {
  const { comparison, error, saved, stop, makeCase } = useReplayComparison(conversationId, replayId);
  const replay = comparison?.replay ?? null;
  return (
    <div className="trace-compare">
      <div className="trace-compare-head">
        <button type="button" className="button button-ghost" onClick={onBack}>
          Back to the trace
        </button>
        {replay && (
          <span className="small">
            Replay #{replay.id} of step #{replay.step_position} · {replay.variant.label} ·{" "}
            {REPLAY_STATUS_LABELS[replay.status] ?? replay.status}
            {replay.asked > 0 && ` · ${replay.asked} refused as asked`}
          </span>
        )}
        <div className="trace-head-actions">
          {replay && replayOpen(replay) && (
            <button type="button" className="button button-ghost" onClick={() => void stop()}>
              Stop
            </button>
          )}
          {replay?.status === "done" && !replay.eval_case && (
            <button
              type="button"
              className="button button-ghost"
              title="An eval case that starts at this replay's checkpoint, pinned"
              onClick={() => void makeCase()}
            >
              Make eval case
            </button>
          )}
        </div>
      </div>
      {error && <p className="notice notice-error trace-pad">{error}</p>}
      {saved && (
        <p className="notice trace-pad">
          Saved as eval case <span className="mono">{saved.name}</span> in <span className="mono">{saved.path}</span>,
          starting at checkpoint {saved.checkpoint_id}.
        </p>
      )}
      {replay && replay.blueprint.length > 0 && (
        <p className="faint small trace-pad">{blueprintLine(replay.blueprint)}</p>
      )}
      {replay?.detail && <p className="faint small trace-pad">{replay.detail}</p>}
      {!comparison && !error && <p className="faint trace-pad">Loading…</p>}
      {comparison && (
        <div className="trace-compare-sides">
          <section className="trace-compare-side" aria-label="Original">
            <SideFacts title={`Original, from step #${comparison.replay.step_position}`} side={comparison.original} />
            <TraceTimeline root={original} selectedStepId={comparison.replay.source_step_id} onSelect={ignore} />
          </section>
          <section className="trace-compare-side" aria-label="Replay">
            <SideFacts title="Replay" side={comparison.replayed} />
            {comparison.replay.conversation_id !== null && comparison.replay.agent_run_id !== null ? (
              <ReplayTimeline
                conversationId={comparison.replay.conversation_id}
                runId={comparison.replay.agent_run_id}
              />
            ) : (
              <p className="faint small trace-pad">The replay's run has not started.</p>
            )}
          </section>
        </div>
      )}
    </div>
  );
}

function SideFacts({ title, side }: { title: string; side: Side }) {
  return (
    <div className="trace-compare-facts">
      <h3>{title}</h3>
      <dl className="trace-facts">
        <dt>Steps</dt>
        <dd>
          {side.steps} ({side.model_calls} model, {side.tool_calls} tool)
        </dd>
        <dt>Tokens</dt>
        <dd>{side.tokens.toLocaleString()}</dd>
        <dt>Cost</dt>
        <dd>{formatCost(side.cost) || <span className="faint">none (subscription or unknown)</span>}</dd>
      </dl>
      <h4>Final answer</h4>
      <pre className="trace-content">{side.final_answer || "(no answer yet)"}</pre>
    </div>
  );
}

function ReplayTimeline({ conversationId, runId }: { conversationId: number; runId: number }) {
  const { trace, error } = useTrace({ conversationId, runId });
  if (error) return <p className="form-error small trace-pad">{error}</p>;
  if (!trace) return <p className="faint small trace-pad">Loading…</p>;
  return <TraceTimeline root={trace.run} selectedStepId={null} onSelect={ignore} />;
}
