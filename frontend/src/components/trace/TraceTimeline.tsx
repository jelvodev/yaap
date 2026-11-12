import { useEffect, useRef } from "react";

import { approvalLabel, RUN_STATUS_LABELS, WORKER_LABELS } from "./runLabels";
import { barOf, formatCost, formatDuration, timeSpan, timelineRows, type TraceRun, type TraceStep } from "./traceOrder";

interface TraceTimelineProps {
  root: TraceRun;
  selectedStepId: number | null;
  onSelect: (stepId: number) => void;
}

/** The runs and steps in the order they happened, each step with its place in time, duration and cost. */
export default function TraceTimeline({ root, selectedStepId, onSelect }: TraceTimelineProps) {
  const span = timeSpan(root);
  const list = useRef<HTMLOListElement | null>(null);
  // A step chosen from elsewhere (a chip, a link) may be off screen.
  useEffect(() => {
    list.current?.querySelector('[aria-pressed="true"]')?.scrollIntoView({ block: "nearest" });
  }, [selectedStepId]);
  return (
    <ol className="trace-timeline" aria-label="Steps" ref={list}>
      {timelineRows(root).map((row) =>
        row.kind === "run" ? (
          <li key={row.key} className="trace-run" style={{ paddingLeft: `${row.depth * 18 + 8}px` }}>
            <RunHeader run={row.run} />
          </li>
        ) : (
          <li key={row.key}>
            <button
              type="button"
              className="trace-step"
              aria-pressed={row.step.id === selectedStepId}
              style={{ paddingLeft: `${row.depth * 18 + 20}px` }}
              onClick={() => onSelect(row.step.id)}
            >
              <StepLine step={row.step} />
              <span className="trace-bar-track" aria-hidden="true">
                <span
                  className={`trace-bar trace-bar-${row.step.kind}${row.step.is_error ? " trace-bar-error" : ""}`}
                  style={{ left: `${barOf(row.step, span).left}%`, width: `${barOf(row.step, span).width}%` }}
                />
              </span>
              <span className="trace-num">{formatDuration(row.step.duration_ms)}</span>
              <span className="trace-num">{formatCost(row.step.cost)}</span>
            </button>
          </li>
        ),
      )}
    </ol>
  );
}

function RunHeader({ run }: { run: TraceRun }) {
  const who = run.agent ?? (run.parent_run_id === null ? "YAAP" : "worker");
  return (
    <>
      <span className="trace-run-title">
        {run.parent_run_id === null ? "Reply" : <>Delegated: {run.task}</>}
      </span>
      <span className="faint small">
        {who}
        {run.worker ? ` · ${WORKER_LABELS[run.worker] ?? run.worker}` : ""} ·{" "}
        <span className="mono">{run.model}</span>
      </span>
      <span className={`run-status run-${run.status}`}>{RUN_STATUS_LABELS[run.status] ?? run.status}</span>
      <span className="trace-num">{formatDuration(run.duration_ms)}</span>
      <span className="trace-num" title="With its sub-runs">
        {formatCost(run.total_cost)}
      </span>
    </>
  );
}

function StepLine({ step }: { step: TraceStep }) {
  if (step.kind === "model") {
    return (
      <span className="trace-step-label">
        <span className="trace-kind">model</span> <span className="mono">{step.model ?? "unknown model"}</span>
        <span className="faint small">
          {" "}
          · {step.input_tokens + step.cache_read_tokens + step.cache_write_tokens} in · {step.output_tokens} out
        </span>
      </span>
    );
  }
  return (
    <span className={`trace-step-label${step.is_error ? " form-error" : ""}`}>
      <span className="trace-kind">tool</span> <span className="mono">{step.tool}</span>
      {step.approval && <span className="faint small"> · {approvalLabel(step.approval)}</span>}
      {step.summary && <span className="faint small"> {step.summary}</span>}
    </span>
  );
}
