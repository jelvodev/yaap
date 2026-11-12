import { type ReactNode, useEffect, useState } from "react";

import { CloseIcon } from "../icons";
import ContextTab from "./ContextTab";
import ReplayCompare from "./ReplayCompare";
import StepDetail from "./StepDetail";
import StepReplays from "./StepReplays";
import TokensTab from "./TokensTab";
import TraceTimeline from "./TraceTimeline";
import { allRuns, formatCost, formatDuration } from "./traceOrder";
import { useReplays } from "./useReplays";
import type { Trace, TraceTarget } from "./useTrace";
import { TRACE_TABS, useTraceTabs } from "./useTraceTabs";
import "./trace.css";

interface TraceDialogProps {
  target: TraceTarget;
  onClose: () => void;
  /** Actions in the header beside the summary, such as a download of the trace; given the loaded trace. */
  headerActions?: (trace: Trace) => ReactNode;
  /** A step to choose once the trace has loaded. */
  initialStepId?: number;
}

/**
 * The trace of a reply or run: its steps as a timeline with duration and cost, sub-runs as a tree in it, and for the
 * chosen step its details and, for a model call, what it saw grouped by source.
 */
export default function TraceDialog({ target, onClose, headerActions, initialStepId }: TraceDialogProps) {
  const tabs = useTraceTabs(target, initialStepId);
  const { trace, error, selectedStepId, selectStep } = tabs;
  const replays = useReplays(target.conversationId);
  // The replay shown beside the original instead of the timeline.
  const [replayId, setReplayId] = useState<number | null>(null);

  useEffect(() => {
    // Captured on the window and stopped there, so a popover this dialog opened from does not also close on Escape.
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      onClose();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  const runs = trace ? allRuns(trace.run) : [];
  const selected = runs.flatMap((run) => run.steps).find((step) => step.id === selectedStepId) ?? null;
  const selectedRun = selected ? runs.find((run) => run.steps.includes(selected)) : undefined;
  const openStep = ({ runId, stepId }: { runId: number; stepId: number }) => {
    setReplayId(null);
    if (runs.some((run) => run.id === runId)) {
      tabs.setTab("timeline");
      selectStep(stepId);
    } else tabs.openInOtherRun(runId, stepId);
  };
  const canReplay = selected !== null && selected.has_context && selectedRun?.parent_run_id === null;
  return (
    <div className="palette-backdrop trace-backdrop" onMouseDown={onClose}>
      <div
        className="palette trace-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Trace"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="trace-head">
          <h2>Trace</h2>
          {trace && (
            <span className="faint small">
              {runs.length > 1 ? `${runs.length} runs · ` : ""}
              {runs.reduce((count, run) => count + run.steps.length, 0)} steps
              {trace.run.duration_ms != null && ` · ${formatDuration(trace.run.duration_ms)}`}
              {trace.run.total_cost != null && ` · ${formatCost(trace.run.total_cost)}`}
            </span>
          )}
          <div className="trace-head-actions">
            {trace && (
              <a
                className="button button-ghost"
                href={`/api/v1/telemetry/runs/${trace.run.id}/otlp`}
                download
                title="Download this reply as OpenTelemetry (OTLP JSON)"
              >
                Download OTLP
              </a>
            )}
            {trace && headerActions?.(trace)}
          </div>
          <button type="button" className="icon-button trace-close" aria-label="Close the trace" onClick={onClose}>
            <CloseIcon />
          </button>
        </header>
        <div className="trace-tabs" role="tablist" aria-label="Trace views" onKeyDown={tabs.onTabKeyDown}>
          {TRACE_TABS.map(({ id, label }) => (
            <button
              key={id}
              type="button"
              id={`trace-tab-${id}`}
              role="tab"
              aria-selected={tabs.tab === id}
              aria-controls="trace-panel"
              tabIndex={tabs.tab === id ? 0 : -1}
              className="trace-tab"
              onClick={() => tabs.setTab(id)}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="trace-panel" id="trace-panel" role="tabpanel" aria-labelledby={`trace-tab-${tabs.tab}`}>
          {tabs.tab === "context" && <ContextTab conversationId={target.conversationId} onOpenStep={openStep} />}
          {tabs.tab === "tokens" && <TokensTab conversationId={target.conversationId} onOpenStep={openStep} />}
          {tabs.tab === "timeline" && error && <p className="notice notice-error trace-pad">{error}</p>}
          {tabs.tab === "timeline" && !trace && !error && <p className="faint trace-pad">Loading…</p>}
          {tabs.tab === "timeline" && trace && replayId !== null && (
            <ReplayCompare
              conversationId={trace.conversation_id}
              replayId={replayId}
              original={trace.run}
              onBack={() => {
                setReplayId(null);
                void replays.reload();
              }}
            />
          )}
          {tabs.tab === "timeline" && trace && replayId === null && (
            <div className="trace-body">
              <TraceTimeline root={trace.run} selectedStepId={selectedStepId} onSelect={selectStep} />
              <StepDetail
                conversationId={trace.conversation_id}
                step={selected}
                replays={
                  selected && (
                    <StepReplays
                      stepId={selected.id}
                      canReplay={canReplay}
                      replays={replays.replays}
                      error={replays.error}
                      onStart={replays.start}
                      onOpen={setReplayId}
                    />
                  )
                }
              />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
