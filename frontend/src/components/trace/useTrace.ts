import { useCallback, useEffect, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";
import { type ToolCallRef, stepForToolCall } from "./traceOrder";

export type Trace = components["schemas"]["TraceOut"];
export type StepContext = components["schemas"]["StepContextOut"];

/**
 * Which trace to show: a reply's, from the chat, or a run's, from an agent's runs in the Blueprint. A reply's trace
 * can open at the step of one of its tool calls.
 */
export type TraceTarget =
  | { conversationId: number; messageId: number; toolCall?: ToolCallRef; tab?: "route" }
  | { conversationId: number; runId: number };

async function fetchTrace(target: TraceTarget) {
  if ("messageId" in target) {
    return api.GET("/api/v1/conversations/{conversation_id}/messages/{message_id}/trace", {
      params: { path: { conversation_id: target.conversationId, message_id: target.messageId } },
    });
  }
  return api.GET("/api/v1/conversations/{conversation_id}/runs/{run_id}/trace", {
    params: { path: { conversation_id: target.conversationId, run_id: target.runId } },
  });
}

/** A trace with the step chosen in it; reloaded every few seconds while a run in it goes on. */
export function useTrace(target: TraceTarget) {
  const [trace, setTrace] = useState<Trace | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedStepId, setSelectedStepId] = useState<number | null>(null);
  // Keyed by value, so a new target object for the same trace loads nothing new.
  const { conversationId } = target;
  const messageId = "messageId" in target ? target.messageId : null;
  const runId = "runId" in target ? target.runId : null;
  const toolCall = "toolCall" in target ? (target.toolCall ?? null) : null;

  const load = useCallback(async () => {
    const { data, error: failure } = await fetchTrace(target);
    if (data) {
      setTrace(data);
      setError(null);
    } else setError(errorMessage(failure, "The trace could not be loaded."));
  }, [conversationId, messageId, runId]);

  useEffect(() => {
    setTrace(null);
    setSelectedStepId(null);
    void load();
  }, [load]);

  // Opened from a tool call: its step is chosen once the trace is there.
  const loadedRunId = trace?.run.id ?? null;
  useEffect(() => {
    if (!trace || !toolCall) return;
    const step = stepForToolCall(trace.run, toolCall);
    if (step) setSelectedStepId(step.id);
    // Only when a trace arrives, not on every reload while it runs.
  }, [loadedRunId, toolCall?.name, toolCall?.occurrence]);

  const running = trace !== null && hasRunning(trace.run);
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => void load(), 4000);
    return () => window.clearInterval(timer);
  }, [running, load]);

  return { trace, error, selectedStepId, selectStep: setSelectedStepId };
}

function hasRunning(run: Trace["run"]): boolean {
  return run.status === "running" || run.children.some(hasRunning);
}

/** What one model call saw, grouped by source, and the content of the parts the user opened. */
export function useStepContext(conversationId: number, stepId: number | null) {
  const [context, setContext] = useState<StepContext | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [contents, setContents] = useState<Record<string, string>>({});

  useEffect(() => {
    setContext(null);
    setError(null);
    setContents({});
    if (stepId === null) return;
    let current = true;
    void api
      .GET("/api/v1/conversations/{conversation_id}/steps/{step_id}/context", {
        params: { path: { conversation_id: conversationId, step_id: stepId } },
      })
      .then(({ data, error: failure }) => {
        if (!current) return;
        if (data) setContext(data);
        else setError(errorMessage(failure, "What this call saw could not be loaded."));
      });
    return () => {
      current = false;
    };
  }, [conversationId, stepId]);

  const openPart = useCallback(
    async (key: string) => {
      if (stepId === null || key in contents) return;
      const path = "/api/v1/conversations/{conversation_id}/steps/{step_id}/context/{key}";
      const { data, error: failure } = await api.GET(path, {
        params: { path: { conversation_id: conversationId, step_id: stepId, key } },
      });
      setContents((known) => ({ ...known, [key]: data ? data.content : errorMessage(failure, "This part is gone.") }));
    },
    [conversationId, stepId, contents],
  );

  return { context, error, contents, openPart };
}
