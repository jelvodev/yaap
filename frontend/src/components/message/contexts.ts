import { createContext } from "react";
import type { Behind, CallWhy } from "../../api/client";

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

/** What happened behind a stored reply (labels and call reasons); null while loading, absent or failed. */
export const BehindContext = createContext<Behind | null>(null);

/** The reason of a tool call of the reply by its call key, if the call was refused, asked or declined. */
export const CallReasonContext = createContext<(key: string) => CallWhy | undefined>(() => undefined);
