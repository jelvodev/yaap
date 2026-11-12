import { type KeyboardEvent, useEffect, useState } from "react";

import { type TraceTarget, useTrace } from "./useTrace";
import { allRuns } from "./traceOrder";

export type TraceTab = "timeline" | "route" | "context" | "tokens";

export const TRACE_TABS: readonly { id: TraceTab; label: string }[] = [
  { id: "timeline", label: "Timeline" },
  { id: "route", label: "Route" },
  { id: "context", label: "Context" },
  { id: "tokens", label: "Tokens" },
];

/** A step to choose once the trace it is in has loaded; `runId` is that trace's root run, null when not known. */
interface PendingStep {
  stepId: number;
  runId: number | null;
}

/** The tabs a target has: the route belongs to a reply, so a trace of a bare run has none. */
export function tabsFor(target: TraceTarget): readonly { id: TraceTab; label: string }[] {
  return "messageId" in target ? TRACE_TABS : TRACE_TABS.filter((entry) => entry.id !== "route");
}

function targetKey(target: TraceTarget): string {
  return "messageId" in target
    ? `${target.conversationId}:message:${target.messageId}`
    : `${target.conversationId}:run:${target.runId}`;
}

/**
 * The trace dialog's tab and the trace it shows: the one it was opened on, or the one of another run once a step of
 * that run was opened. A step asked for is chosen as soon as its trace has loaded.
 */
export function useTraceTabs(target: TraceTarget, initialStepId?: number) {
  const [tab, setTab] = useState<TraceTab>("messageId" in target ? (target.tab ?? "timeline") : "timeline");
  const [override, setOverride] = useState<{
    key: string;
    target: TraceTarget;
  } | null>(null);
  const [pending, setPending] = useState<PendingStep | null>(
    initialStepId === undefined ? null : { stepId: initialStepId, runId: null },
  );
  const key = targetKey(target);
  // A changed target shows its own trace; the override only holds for the target it was made on.
  const shown = override && override.key === key ? override.target : target;
  const view = useTrace(shown);
  const { trace, selectStep } = view;

  const loadedRunId = trace?.run.id ?? null;
  useEffect(() => {
    if (!trace || pending === null || (pending.runId !== null && pending.runId !== trace.run.id)) return;
    if (allRuns(trace.run).some((run) => run.steps.some((step) => step.id === pending.stepId))) {
      selectStep(pending.stepId);
    }
    setPending(null);
    // Keyed on the run of the loaded trace, so a reload of the same trace does not choose again.
  }, [loadedRunId, pending]);

  const openInOtherRun = (runId: number, stepId: number) => {
    setOverride({
      key,
      target: { conversationId: target.conversationId, runId },
    });
    setPending({ stepId, runId });
    setTab("timeline");
  };

  // Arrow keys move between the tabs; only the chosen tab is in the tab order.
  const onTabKeyDown = (event: KeyboardEvent) => {
    const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (step === 0) return;
    event.preventDefault();
    const available = tabsFor(target);
    const index = available.findIndex((entry) => entry.id === tab);
    const next = available[(index + step + available.length) % available.length].id;
    setTab(next);
    document.getElementById(`trace-tab-${next}`)?.focus();
  };

  return { ...view, tab, setTab, openInOtherRun, onTabKeyDown };
}
