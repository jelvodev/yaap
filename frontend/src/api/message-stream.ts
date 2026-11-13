import { type ConversationSummary, type Message, type RouteStopOut, errorMessage } from "./client";
import type { components } from "./schema";
import type { UtilitySkip } from "./utility-skips";

type Schemas = components["schemas"];

/** One event of a reply stream, as the server publishes it: the `event` field names the kind. */
export type ReplyEvent = Schemas["ReplyEvent"];
export type ToolCallStart = Schemas["ToolCallEvent"];
export type ToolCallResult = Schemas["ToolResultEvent"];
/** An action of an agent run that waits for the user's decision. */
export type ApprovalRequest = Schemas["ApprovalRequestEvent"];
export type ApprovalDecision = Schemas["ApprovalResolvedEvent"]["decision"];
/**
 * The shared document beside the chat: the agent opened it, starts or finished a change of it, or got
 * the user's saved changes with a tool result.
 */
export type DocumentEvent =
  | Schemas["DocumentOpenEvent"]
  | Schemas["DocumentWritingEvent"]
  | Schemas["DocumentChangeEvent"]
  | Schemas["DocumentSeenEvent"];

/** Events of POST /api/v1/conversations/{id}/messages, sent as server-sent events. */
export interface MessageStreamHandlers {
  onUserMessage: (message: Message) => void;
  onReasoningDelta: (text: string) => void;
  onTextDelta: (text: string) => void;
  onToolCall: (call: ToolCallStart) => void;
  onToolResult: (result: ToolCallResult) => void;
  onApprovalRequest: (request: ApprovalRequest) => void;
  onApprovalResolved: (approvalId: string, decision: ApprovalDecision) => void;
  onDone: (message: Message, conversation: ConversationSummary) => void;
  /** A better title from the utility model, after the first reply. */
  onTitle: (conversation: ConversationSummary) => void;
  /** A model call broke off and starts again: drop the text and reasoning it had streamed. */
  onCallRestarted?: () => void;
  // A delegated sub-run reports which tool it is using.
  onDelegateProgress?: (label: string, step: string) => void;
  // Output of a running command, as it comes; the key is callKey(tool, input).
  onToolOutput?: (callKey: string, text: string) => void;
  // A steering message reached the agent with a tool result.
  onSteering?: (text: string) => void;
  // /compact could not summarise; the conversation is reloaded when the stream ends either way.
  onCompactFailed?: (detail: string) => void;
  // The shared document.
  onDocument?: (event: DocumentEvent) => void;
  // /eval started an eval run instead of a reply: which run, and what to show.
  onEvalStarted?: (runId: number, projectId: number, detail: string) => void;
  // The model policy skipped a utility task of this exchange (the title, routing, a check of outside content).
  onUtilitySkipped?: (skip: UtilitySkip) => void;
  // A stop of the reply's route was reached or changed (the same id replaces the earlier stop).
  onStage?: (stop: RouteStopOut) => void;
  // A live stop is gone again.
  onStageRemoved?: (id: string) => void;
}

// Checked against the union, so a new server event fails the typecheck until the client lists it.
const EVENT_NAMES: Record<ReplyEvent["event"], true> = {
  text_delta: true,
  reasoning_delta: true,
  tool_call: true,
  tool_result: true,
  call_restarted: true,
  tool_output: true,
  steering: true,
  checkpoint: true,
  delegate_progress: true,
  plan_submitted: true,
  approval_request: true,
  approval_resolved: true,
  document_open: true,
  document_writing: true,
  document_change: true,
  document_seen: true,
  user_message: true,
  done: true,
  title: true,
  utility_skipped: true,
  stage: true,
  stage_removed: true,
  compacted: true,
  compact_failed: true,
  eval_started: true,
};

/** Whether the parsed data is an event of a kind this client knows, named like the `event:` line. */
function isReplyEvent(value: unknown, name: string): value is ReplyEvent {
  if (typeof value !== "object" || value === null || !("event" in value)) return false;
  return value.event === name && Object.hasOwn(EVENT_NAMES, name);
}

function dispatchEvent(chunk: string, handlers: MessageStreamHandlers): void {
  let name = "";
  let data = "";
  for (const line of chunk.split("\n")) {
    if (line.startsWith("event: ")) name = line.slice(7);
    else if (line.startsWith("data: ")) data += line.slice(6);
  }
  if (!name || !data) return;
  const payload: unknown = JSON.parse(data);
  // An event of a newer server is ignored.
  if (!isReplyEvent(payload, name)) return;
  switch (payload.event) {
    case "user_message":
      handlers.onUserMessage(payload);
      break;
    case "reasoning_delta":
      handlers.onReasoningDelta(payload.text);
      break;
    case "text_delta":
      handlers.onTextDelta(payload.text);
      break;
    case "tool_call":
      handlers.onToolCall(payload);
      break;
    case "tool_result":
      handlers.onToolResult(payload);
      break;
    case "approval_request":
      handlers.onApprovalRequest(payload);
      break;
    case "approval_resolved":
      handlers.onApprovalResolved(payload.approval_id, payload.decision);
      break;
    case "call_restarted":
      handlers.onCallRestarted?.();
      break;
    case "delegate_progress":
      handlers.onDelegateProgress?.(payload.label, payload.step);
      break;
    case "steering":
      handlers.onSteering?.(payload.text);
      break;
    case "tool_output":
      handlers.onToolOutput?.(payload.call_key, payload.text);
      break;
    case "title":
      handlers.onTitle(payload.conversation);
      break;
    case "document_open":
    case "document_writing":
    case "document_change":
    case "document_seen":
      handlers.onDocument?.(payload);
      break;
    case "eval_started":
      handlers.onEvalStarted?.(payload.run_id, payload.project_id, payload.detail);
      break;
    case "utility_skipped":
      handlers.onUtilitySkipped?.(payload);
      break;
    case "stage":
      handlers.onStage?.(payload);
      break;
    case "stage_removed":
      handlers.onStageRemoved?.(payload.id);
      break;
    case "compact_failed":
      handlers.onCompactFailed?.(payload.detail);
      break;
    case "done":
      handlers.onDone(payload.message, payload.conversation);
      break;
    case "checkpoint":
    case "plan_submitted":
    case "compacted":
      // The browser reloads the conversation when the stream ends; these need no live action.
      break;
  }
}

async function readEvents(response: Response, handlers: MessageStreamHandlers): Promise<void> {
  if (!response.body) throw new Error("The server sent no reply stream.");
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value.replaceAll("\r\n", "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      dispatchEvent(buffer.slice(0, boundary), handlers);
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");
    }
  }
}

/** Send a message and follow the reply. Aborting only stops following: the reply itself keeps going. */
export async function sendMessage(
  conversationId: number,
  text: string,
  attachmentIds: number[],
  handlers: MessageStreamHandlers,
  signal: AbortSignal,
  editOf?: number,
): Promise<void> {
  const response = await fetch(`/api/v1/conversations/${conversationId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, attachment_ids: attachmentIds, edit_of: editOf ?? null }),
    signal,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(errorMessage(body, `The message could not be sent (HTTP ${response.status}).`));
  }
  await readEvents(response, handlers);
}

/** Atomically accept selected queued items with the user message and follow its reply. */
export async function sendQueuedMessage(
  conversationId: number,
  selection: { item_id?: number; all?: boolean },
  handlers: MessageStreamHandlers,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(`/api/v1/conversations/${conversationId}/queue/send`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item_id: selection.item_id ?? null, all: selection.all ?? false }),
    signal,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(errorMessage(body, `The queued message could not be sent (HTTP ${response.status}).`));
  }
  await readEvents(response, handlers);
}

/** Answer the question of a reply again; the new reply becomes a version next to the old one. */
export async function regenerateReply(
  conversationId: number,
  replyId: number,
  handlers: MessageStreamHandlers,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(`/api/v1/conversations/${conversationId}/messages/${replyId}/regenerate`, {
    method: "POST",
    signal,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(errorMessage(body, `The reply could not be made again (HTTP ${response.status}).`));
  }
  await readEvents(response, handlers);
}

/** Retry a reply against the existing stored user message without creating another user message. */
export async function retryMessage(
  conversationId: number,
  messageId: number,
  handlers: MessageStreamHandlers,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(`/api/v1/conversations/${conversationId}/messages/${messageId}/retry`, {
    method: "POST",
    signal,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(errorMessage(body, `The reply could not be started again (HTTP ${response.status}).`));
  }
  await readEvents(response, handlers);
}

/** Follow a reply that is running in the background, from its first event. False when none is running. */
export async function followReply(
  conversationId: number,
  handlers: MessageStreamHandlers,
  signal: AbortSignal,
): Promise<boolean> {
  const response = await fetch(`/api/v1/conversations/${conversationId}/reply`, { signal });
  if (response.status === 204) return false;
  if (!response.ok) throw new Error(`The running reply could not be followed (HTTP ${response.status}).`);
  await readEvents(response, handlers);
  return true;
}

/** Stop the running reply on the server; what arrived is kept as an incomplete reply. */
export async function stopReply(conversationId: number): Promise<void> {
  await fetch(`/api/v1/conversations/${conversationId}/reply/stop`, { method: "POST" });
}
