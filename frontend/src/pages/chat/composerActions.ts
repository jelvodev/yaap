import { type Conversation, api, errorMessage } from "../../api/client";
import { askForNotifications } from "../../notifications";
import type { ActiveConversation } from "./useActiveConversation";
import type { ConversationStream } from "./useConversationStream";
import type { MessageQueue } from "./useMessageQueue";
import { type PendingAttachments, attachmentIds } from "./usePendingAttachments";

export interface ComposerActionsOptions {
  active: ActiveConversation;
  /** Create the conversation the first message or attachment goes to. */
  startConversation: () => Promise<Conversation | null>;
  attachments: PendingAttachments;
  queue: MessageQueue;
  stream: ConversationStream;
}

export interface ComposerActions {
  send: (text: string) => Promise<void>;
  addFiles: (files: File[]) => Promise<void>;
  /** Queue a message behind the running reply. */
  queueText: (text: string) => Promise<void>;
  /** Give a message to the running agent; when nothing can take it now, it is queued. */
  steer: (text: string) => Promise<void>;
}

/** What the composer does with a message: send it (creating the conversation first), queue it, or steer with it. */
export function composerActions(options: ComposerActionsOptions): ComposerActions {
  const { active, startConversation, attachments, queue, stream } = options;
  const conversation = active.conversation;

  async function send(text: string) {
    active.setError(null);
    const target = conversation ?? (await startConversation());
    if (!target) return;
    const sent = attachments.ready(target.id);
    attachments.release(sent);
    askForNotifications();
    await stream.sendText(target.id, text, attachmentIds(sent));
  }

  async function addFiles(files: File[]) {
    active.setError(null);
    const target = conversation ?? (await startConversation());
    if (!target) return;
    await attachments.add(target.id, files);
  }

  async function queueText(text: string) {
    if (!conversation) return;
    const sent = attachments.ready(conversation.id);
    // The attachments now belong to the queued message.
    if (await queue.add(conversation.id, text, attachmentIds(sent))) attachments.release(sent);
  }

  async function steer(text: string) {
    if (!conversation) return;
    const { error: steerError, response } = await api.POST("/api/v1/conversations/{conversation_id}/steer", {
      params: { path: { conversation_id: conversation.id } },
      body: { text, item_id: null },
    });
    if (response.status === 409) {
      await queueText(text);
      return;
    }
    if (steerError) active.setError(errorMessage(steerError, "The message could not be sent to YAAP."));
  }

  return { send, addFiles, queueText, steer };
}
