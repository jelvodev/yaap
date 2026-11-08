import type { Message } from "../api/client";
import AssistantMessage from "./message/AssistantMessage";
import type { MessageActions } from "./message/MessageActions";
import UserMessage from "./message/UserMessage";

/**
 * One stored message of a conversation. `focused` marks the message a search result pointed to. The parts live in
 * `./message/`: the reply's blocks (`ReplyBody`, `Markdown`, `ToolCallBlock`), the actions under a message
 * (`MessageActions`), attachments, and the shapes and contexts they share (`replyBlock`, `contexts`).
 */
export default function MessageView({
  message,
  focused = false,
  actions,
}: {
  message: Message;
  focused?: boolean;
  actions?: MessageActions;
}) {
  const anchor = { id: `message-${message.id}`, "data-focused": focused || undefined } as const;
  if (message.role === "user") return <UserMessage message={message} anchor={anchor} actions={actions} />;
  return <AssistantMessage message={message} anchor={anchor} actions={actions} />;
}
