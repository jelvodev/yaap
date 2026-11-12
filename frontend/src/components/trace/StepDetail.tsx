import type { ReactNode } from "react";

import { approvalLabel } from "./runLabels";
import { formatCost, formatDuration, type TraceStep } from "./traceOrder";
import { type StepContext, useStepContext } from "./useTrace";

interface StepDetailProps {
  conversationId: number;
  step: TraceStep | null;
  /** Shown under a model call's facts: replaying from it and its replays. */
  replays?: ReactNode;
}

/** The chosen step: a tool call's details, or a model call's tokens and cost with what it saw. */
export default function StepDetail({ conversationId, step, replays }: StepDetailProps) {
  if (step === null) {
    return (
      <section className="trace-detail">
        <p className="faint small">Choose a step to see its details and, for a model call, what it saw.</p>
      </section>
    );
  }
  return (
    <section className="trace-detail" aria-label="Step">
      <h3>
        {step.kind === "model" ? "Model call" : "Tool call"} <span className="faint">#{step.position}</span>
      </h3>
      <dl className="trace-facts">
        <dt>Started</dt>
        <dd>{new Date(step.started_at).toLocaleTimeString()}</dd>
        <dt>Took</dt>
        <dd>{formatDuration(step.duration_ms)}</dd>
        {step.kind === "model" ? <ModelFacts step={step} /> : <ToolFacts step={step} />}
      </dl>
      {step.kind === "model" && replays}
      {step.kind === "model" && (
        <ContextView conversationId={conversationId} stepId={step.has_context ? step.id : null} />
      )}
    </section>
  );
}

function ModelFacts({ step }: { step: TraceStep }) {
  return (
    <>
      <dt>Model</dt>
      <dd className="mono">{step.model ?? "unknown"}</dd>
      <dt>Tokens</dt>
      <dd>
        {step.input_tokens.toLocaleString()} in · {step.cache_read_tokens.toLocaleString()} from cache ·{" "}
        {step.cache_write_tokens.toLocaleString()} to cache · {step.output_tokens.toLocaleString()} out
      </dd>
      <dt>Cost</dt>
      <dd>{formatCost(step.cost) || <span className="faint">none (subscription or unknown)</span>}</dd>
      {step.model_calls !== null && step.model_calls !== 1 && (
        <>
          <dt>Calls</dt>
          <dd>{step.model_calls}, as the coding agent CLI reported</dd>
        </>
      )}
    </>
  );
}

function ToolFacts({ step }: { step: TraceStep }) {
  return (
    <>
      <dt>Tool</dt>
      <dd className="mono">{step.tool}</dd>
      {step.summary && (
        <>
          <dt>Call</dt>
          <dd className="trace-summary">{step.summary}</dd>
        </>
      )}
      <dt>Result</dt>
      <dd className={step.is_error ? "form-error" : undefined}>
        {step.is_error ? "error" : "ok"}
        {step.output_ref !== null && <span className="faint"> · whole output stored as #{step.output_ref}</span>}
      </dd>
      {step.approval && (
        <>
          <dt>Approval</dt>
          <dd>{approvalLabel(step.approval)}</dd>
        </>
      )}
    </>
  );
}

/** What one model call saw, grouped by source with sizes; a part's content loads when it is opened. */
function ContextView({ conversationId, stepId }: { conversationId: number; stepId: number | null }) {
  const { context, error, contents, openPart } = useStepContext(conversationId, stepId);
  if (stepId === null) return <p className="faint small">No snapshot was recorded for this call.</p>;
  if (error) return <p className="form-error small">{error}</p>;
  if (context === null) return <p className="faint small">Loading what it saw…</p>;
  if (!context.available) {
    return <p className="faint small">What this call saw is gone: snapshots are kept 30 days after their last use.</p>;
  }
  return (
    <div className="trace-context">
      <h4>
        What it saw <span className="faint small">· {formatChars(context.chars)}</span>
      </h4>
      {!context.complete && <p className="faint small">Some parts are gone: they were pruned.</p>}
      {context.groups.map((group) => (
        <ContextGroup key={group.source} group={group} contents={contents} onOpen={(key) => void openPart(key)} />
      ))}
    </div>
  );
}

function ContextGroup({
  group,
  contents,
  onOpen,
}: {
  group: StepContext["groups"][number];
  contents: Record<string, string>;
  onOpen: (key: string) => void;
}) {
  return (
    <details className="trace-context-group">
      <summary>
        <span className="trace-source">{group.source}</span>{" "}
        <span className="faint small">
          {group.parts.length} {group.parts.length === 1 ? "part" : "parts"} · {formatChars(group.chars)}
        </span>
      </summary>
      {group.parts.map((part) => (
        <details
          key={part.key}
          className="trace-context-part"
          onToggle={(event) => event.currentTarget.open && onOpen(part.key)}
        >
          <summary>
            {part.label}{" "}
            <span className="faint small">· {part.chars === null ? "gone" : formatChars(part.chars)}</span>
          </summary>
          <pre className="trace-content">{contents[part.key] ?? "Loading…"}</pre>
        </details>
      ))}
    </details>
  );
}

function formatChars(chars: number): string {
  return chars >= 1000 ? `${(chars / 1000).toFixed(1)}k chars` : `${chars} chars`;
}
