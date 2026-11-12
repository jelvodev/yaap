import { type MutableRefObject, useEffect, useRef } from "react";

import { type ConversationSummary, type Message, errorMessage } from "../../api/client";
import {
  type DocumentEvent,
  type MessageStreamHandlers,
  followReply,
  regenerateReply,
  sendMessage,
  sendQueuedMessage,
  retryMessage,
  stopReply,
} from "../../api/message-stream";
import type { Goal } from "../../components/GoalStrip";
import type { QueuedMessage } from "../../components/QueueStrip";
import { forgetRoute } from "../../components/route/useRoute";
import type { RouteStages } from "../../components/route/useRouteStages";
import { notifyIfHidden } from "../../notifications";
import { readAloud, readsEveryReply } from "../../speech/readAloud";
import { watchFileChanges } from "./fileChanges";
import type { ActiveConversation } from "./useActiveConversation";
import type { MessageQueue, QueueSelection } from "./useMessageQueue";
import type { ReplyBlocks } from "./useReplyBlocks";
import { type RunningReplies, listRunningReplies } from "./useRunningReplies";

type OpenStream = (handlers: MessageStreamHandlers, signal: AbortSignal) => Promise<boolean | void>;

export interface ConversationStreamOptions {
  environmentId: number;
  selectedId: number | null;
  active: ActiveConversation;
  reply: ReplyBlocks;
  queue: MessageQueue;
  running: RunningReplies;
  /** The stops of the route of the reply that streams in. */
  stages: RouteStages;
  loadGoal: (conversationId: number) => Promise<Goal | null>;
  onSummary: (summary: ConversationSummary) => void;
  onDocument: (event: DocumentEvent) => void;
  /** A reply was stored: its usage counts now. */
  onReplyStored: () => void;
  /** The agent changed a project file (a write_file or edit_file succeeded): the preview reloads. */
  onFileChanged?: () => void;
  /** /eval started an eval run: the project panel shows its Evals tab. */
  onEvalStarted?: () => void;
  /** The model policy skipped a utility task of the reply. */
  onUtilitySkipped?: MessageStreamHandlers["onUtilitySkipped"];
}

export interface ConversationStream {
  sendText: (conversationId: number, text: string, attachmentIds: number[]) => Promise<void>;
  /** Follow a reply that runs on the server already, such as the next turn of a resumed goal. */
  followRunning: (conversationId: number) => Promise<void>;
  edit: (message: Message, text: string) => void;
  regenerate: (message: Message) => void;
  /** Stop the reply; the server stores what arrived and the stream then ends by itself. */
  stop: () => void;
  sendQueuedNow: (item: QueuedMessage, all?: boolean) => Promise<void>;
  retryStoredMessage: (message: Message) => void;
}

interface StreamRefs {
  abort: MutableRefObject<AbortController | null>;
  /** The conversation whose reply this page follows. */
  following: MutableRefObject<number | null>;
  /** Why the running reply was stopped: by the user (nothing follows) or to send a queued message now. */
  stopIntent: MutableRefObject<"none" | "user" | "send-now">;
}

/** What every step of following a reply works with: the options of this render and the refs that outlive it. */
type StreamContext = ConversationStreamOptions & { refs: StreamRefs };

function detach({ refs, reply }: StreamContext) {
  refs.abort.current?.abort();
  refs.abort.current = null;
  refs.following.current = null;
  reply.clear();
}

function streamHandlers(context: StreamContext): MessageStreamHandlers {
  const { reply, active, stages, onDocument, onSummary, onFileChanged, onEvalStarted, onUtilitySkipped } = context;
  const files = watchFileChanges(() => onFileChanged?.());
  return {
    ...reply.handlers,
    onToolCall: (call) => {
      files.call(call);
      reply.handlers.onToolCall(call);
    },
    onToolResult: (result) => {
      reply.handlers.onToolResult(result);
      files.result(result);
    },
    onUserMessage: (message) => {
      reply.keep();
      active.appendMessage(message);
    },
    onCompactFailed: (detail) => active.setError(detail),
    onEvalStarted: (_runId, _projectId, detail) => {
      active.setError(detail);
      onEvalStarted?.();
    },
    onDocument,
    onUtilitySkipped,
    onStage: stages.upsert,
    onStageRemoved: stages.remove,
    onTitle: (summary) => {
      active.applyTitle(summary);
      onSummary(summary);
    },
    onDone: (message, summary) => {
      // The reply is complete; a title may still follow, but the composer is free again.
      reply.clear();
      forgetRoute(message.id);
      active.appendMessage(message);
      if (readsEveryReply() && message.status === "complete") void readAloud(message.id);
      active.applySummary(summary);
      onSummary(summary);
    },
  };
}

/** Follow a reply: one just sent, or one already running. Ends when the reply ends or the page detaches. */
async function follow(context: StreamContext, conversationId: number, open: OpenStream) {
  const { refs, active, reply } = context;
  refs.abort.current?.abort();
  const controller = new AbortController();
  refs.abort.current = controller;
  refs.following.current = conversationId;
  let ended = false;
  try {
    const followed = await open(streamHandlers(context), controller.signal);
    if (followed === false || controller.signal.aborted) return;
    // Refresh the context meter and the usage card with the stored usage of this turn.
    void active.load(conversationId);
    context.onReplyStored();
    notifyIfHidden("YAAP has replied", "The reply is ready.");
    ended = true;
  } catch (streamError) {
    if (!controller.signal.aborted) {
      active.setError(errorMessage(streamError));
      void active.load(conversationId);
    }
  } finally {
    if (refs.abort.current === controller) {
      refs.abort.current = null;
      refs.following.current = null;
      reply.clear();
    }
    void context.running.reload();
  }
  if (ended || refs.stopIntent.current === "send-now") void afterReply(context, conversationId);
}

function followRunning(context: StreamContext, conversationId: number): Promise<void> {
  return follow(context, conversationId, (handlers, signal) => followReply(conversationId, handlers, signal));
}

async function sendText(context: StreamContext, conversationId: number, text: string, attachmentIds: number[]) {
  context.active.setError(null);
  context.reply.start();
  context.running.markRunning(conversationId);
  context.active.showSentMessage(conversationId, text);
  await follow(context, conversationId, (handlers, signal) =>
    sendMessage(conversationId, text, attachmentIds, handlers, signal),
  );
}

function retryStoredMessage(context: StreamContext, message: Message) {
  const conversation = context.active.conversation;
  if (!conversation || conversation.messages.at(-1)?.id !== message.id || message.role !== "user") return;
  context.active.setError(null);
  context.reply.start();
  context.running.markRunning(conversation.id);
  void follow(context, conversation.id, (handlers, signal) =>
    retryMessage(conversation.id, message.id, handlers, signal),
  ).then(() => context.active.load(conversation.id));
}

/** What happens when a reply ends: a "send now" message goes first, otherwise the next queued one. */
async function afterReply(context: StreamContext, conversationId: number) {
  const { refs } = context;
  const intent = refs.stopIntent.current;
  refs.stopIntent.current = "none";
  if (intent === "send-now") {
    for (let attempt = 0; attempt < 20; attempt += 1) {
      if ((await listRunningReplies(context.environmentId)).includes(conversationId)) {
        await followRunning(context, conversationId);
        return;
      }
      await context.queue.refresh(conversationId);
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    return;
  }
  if (intent === "user") {
    void context.loadGoal(conversationId);
    return;
  }
  const next = await context.queue.refresh(conversationId);
  if (next[0]) {
    await sendQueued(context, conversationId, { item_id: next[0].id });
    return;
  }
  await followGoalTurn(context, conversationId);
}

async function sendQueued(context: StreamContext, conversationId: number, selection: QueueSelection) {
  context.active.setError(null);
  context.reply.start();
  context.running.markRunning(conversationId);
  await follow(context, conversationId, (handlers, signal) =>
    sendQueuedMessage(conversationId, selection, handlers, signal),
  );
  await context.queue.refresh(conversationId);
}

/** In goal mode the server sends the next turn by itself: follow it once it has started. */
async function followGoalTurn(context: StreamContext, conversationId: number) {
  const current = await context.loadGoal(conversationId);
  if (current?.status !== "active") return;
  for (let attempt = 0; attempt < 20; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 250));
    if ((await listRunningReplies(context.environmentId)).includes(conversationId)) {
      await followRunning(context, conversationId);
      return;
    }
    const latest = await context.loadGoal(conversationId);
    if (latest?.status !== "active") return;
  }
}

/** Opening another conversation stops following the last one and follows the new one's reply, if it runs. */
function openSelected(context: StreamContext) {
  const { active, selectedId } = context;
  active.setError(null);
  if (selectedId === null) {
    detach(context);
    active.clear();
    return;
  }
  // A conversation created on this page is already in state; only load one that is not.
  void context.loadGoal(selectedId);
  if (active.conversation?.id !== selectedId) {
    detach(context);
    void active.load(selectedId).then(() => followRunning(context, selectedId));
  }
}

/** Edit or regenerate a message: the conversation is shown up to it while the new version streams in. */
function replaceFrom(context: StreamContext, message: Message, open: (conversationId: number) => OpenStream) {
  const conversation = context.active.conversation;
  if (!conversation) return;
  context.active.setError(null);
  context.active.cutBefore(message.id);
  context.reply.start();
  void follow(context, conversation.id, open(conversation.id));
}

async function sendQueuedNow(context: StreamContext, item: QueuedMessage, all: boolean) {
  const { refs, active } = context;
  if (!active.conversation) return;
  const id = active.conversation.id;
  const selection: QueueSelection = all ? { all: true } : { item_id: item.id };
  const restore = context.queue.withdraw(selection);
  if (refs.following.current === id) {
    const accepted = await context.queue.sendNow(id, selection);
    if (!accepted) {
      restore();
      return;
    }
    refs.stopIntent.current = "send-now";
  } else {
    await sendQueued(context, id, selection);
  }
}

/**
 * Following the replies of the open conversation. Replies run on the server, apart from the page: the page follows
 * the one of the open conversation, and after a reply sends the next queued message or follows a goal's next turn.
 */
export function useConversationStream(options: ConversationStreamOptions): ConversationStream {
  const abort = useRef<AbortController | null>(null);
  const following = useRef<number | null>(null);
  const stopIntent = useRef<"none" | "user" | "send-now">("none");
  const context: StreamContext = { ...options, refs: { abort, following, stopIntent } };

  useEffect(() => openSelected(context), [options.selectedId]);

  // Leaving the page stops following; the reply itself goes on and can be followed again later.
  useEffect(() => () => abort.current?.abort(), []);

  return {
    sendText: (conversationId, text, attachmentIds) => sendText(context, conversationId, text, attachmentIds),
    followRunning: (conversationId) => followRunning(context, conversationId),
    edit: (message, text) =>
      replaceFrom(context, message, (id) => (handlers, signal) =>
        sendMessage(id, text, [], handlers, signal, message.id),
      ),
    regenerate: (message) =>
      replaceFrom(context, message, (id) => (handlers, signal) => regenerateReply(id, message.id, handlers, signal)),
    stop: () => {
      stopIntent.current = "user";
      if (following.current !== null) void stopReply(following.current);
    },
    sendQueuedNow: (item, all = false) => sendQueuedNow(context, item, all),
    retryStoredMessage: (message) => retryStoredMessage(context, message),
  };
}
