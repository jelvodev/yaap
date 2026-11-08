import { createContext } from "react";

// True while the reply that contains the code is still arriving; a half-written page is not previewed.
export const StreamingContext = createContext(false);

/** Checkpoints of a project conversation: which tool call has one, and how to show it. */
export interface CheckpointLinks {
  forCall: (toolCallId: string) => number | undefined;
  open: (checkpointId: number, view: "changes" | "restore") => void;
}

export const CheckpointContext = createContext<CheckpointLinks | null>(null);

/** The conversation whose stored full outputs the tool calls link to. */
export const OutputContext = createContext<number | null>(null);

/** Open the reply's trace at the step of one of its tool calls, by the call's id; absent while a reply streams. */
export const TraceStepContext = createContext<((toolCallId: string) => void) | null>(null);
