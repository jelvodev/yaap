import type { Conversation } from "../../api/client";
import { type RouteStages, useRouteStages } from "../../components/route/useRouteStages";
import { useConversationDocument } from "../../components/document/useConversationDocument";
import { type ComposerActions, composerActions } from "./composerActions";
import type { ActiveConversation } from "./useActiveConversation";
import type { ConversationList } from "./useConversationList";
import { type ConversationGoal, useConversationGoal } from "./useConversationGoal";
import { type ConversationStream, useConversationStream } from "./useConversationStream";
import { type MessageQueue, useMessageQueue } from "./useMessageQueue";
import { type PendingAttachments, usePendingAttachments } from "./usePendingAttachments";
import { type ReplyBlocks, useReplyBlocks } from "./useReplyBlocks";
import type { RunningReplies } from "./useRunningReplies";
import { type UtilitySkips, useUtilitySkips } from "./useUtilitySkips";

export interface ChatSessionOptions {
  environmentId: number;
  selectedId: number | null;
  active: ActiveConversation;
  list: ConversationList;
  running: RunningReplies;
  /** A reply was stored: its usage counts now. */
  onReplyStored: () => void;
  /** Create the conversation the first message or attachment goes to. */
  startConversation: () => Promise<Conversation | null>;
  /** The agent changed a project file: the preview reloads. */
  onFileChanged?: () => void;
  /** /eval started an eval run. */
  onEvalStarted?: () => void;
}

export interface ChatSession {
  reply: ReplyBlocks;
  /** A reply streams in: the composer offers stop, queue and steer instead of send. */
  busy: boolean;
  shared: ReturnType<typeof useConversationDocument>;
  goal: ConversationGoal;
  queue: MessageQueue;
  attachments: PendingAttachments;
  stream: ConversationStream;
  composer: ComposerActions;
  /** Utility tasks of the last exchange that the model policy skipped. */
  skips: UtilitySkips;
  /** The route of the reply that streams in. */
  stages: RouteStages;
}

/** Working in the open conversation: its reply, shared document, goal, queue, attachments and the composer. */
export function useChatSession(options: ChatSessionOptions): ChatSession {
  const { environmentId, selectedId, active, list, running } = options;
  const conversationId = active.conversation?.id ?? null;
  const reply = useReplyBlocks();
  const busy = reply.blocks !== null;
  const stages = useRouteStages(busy, conversationId);
  // The shared document beside the chat.
  const shared = useConversationDocument(conversationId, Boolean(active.conversation?.project_id), busy);
  const goal = useConversationGoal(conversationId, active.setError);
  const queue = useMessageQueue(conversationId, active.setError);
  const attachments = usePendingAttachments(conversationId);
  const skips = useUtilitySkips(conversationId, `${active.conversation?.messages.length ?? 0}-${busy}`);
  const stream = useConversationStream({
    environmentId,
    selectedId,
    active,
    reply,
    queue,
    running,
    stages,
    loadGoal: goal.load,
    onSummary: list.remember,
    onDocument: shared.onEvent,
    onReplyStored: options.onReplyStored,
    onFileChanged: options.onFileChanged,
    onEvalStarted: options.onEvalStarted,
    onUtilitySkipped: skips.add,
  });
  const composer = composerActions({
    active,
    startConversation: options.startConversation,
    attachments,
    queue,
    stream,
  });
  return { reply, busy, shared, goal, queue, attachments, stream, composer, skips, stages };
}
