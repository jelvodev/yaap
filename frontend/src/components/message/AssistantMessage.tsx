import type { Message } from "../../api/client";
import type { ToolCallRef } from "../trace/traceOrder";
import { BehindChips } from "./BehindChipRow";
import { BehindProvider } from "./BehindProvider";
import { TraceStepContext } from "./contexts";
import { Markdown } from "./Markdown";
import type { MessageActions } from "./MessageActions";
import { ReplyActions } from "./ReplyActions";
import { ReplyBody } from "./ReplyBody";
import { SecondOpinionReport } from "./SecondOpinionReport";
import { blocksFromMessage } from "./replyBlock";
import type { MessageAnchor } from "./UserMessage";

const STATUS_LABELS: Record<string, string> = {
  incomplete: "Incomplete",
  refused: "Declined",
  error: "Error",
};

/** A tool call of the message by its id: its tool and which call of that tool it was, as the trace finds its step. */
function toolCallRef(message: Message, toolCallId: string): ToolCallRef | undefined {
  const calls = message.content.filter((block) => block.type === "tool_call");
  const index = calls.findIndex((block) => block.id === toolCallId);
  if (index < 0) return undefined;
  const name = calls[index].name ?? "tool";
  return { name, occurrence: calls.slice(0, index).filter((block) => (block.name ?? "tool") === name).length };
}

/** A stored reply: its blocks, the summary marker of a compacted conversation, its status, route and actions. */
export default function AssistantMessage({
  message,
  anchor,
  actions,
}: {
  message: Message;
  anchor: MessageAnchor;
  actions?: MessageActions;
}) {
  const summary = message.content.find((block) => block.type === "summary");
  // The first message of a conversation continued from a summary holds only that summary and no model run, so
  // trying again, a second opinion or versions mean nothing there. A compacted reply keeps its own blocks and actions.
  const onlySummary = summary !== undefined && message.content.every((block) => block.type === "summary");
  const showTrace = actions?.onShowTrace;
  const openStep = showTrace ? (toolCallId: string) => showTrace(message, toolCallRef(message, toolCallId)) : null;
  return (
    <div className="message-assistant" {...anchor}>
      <div className="avatar" aria-hidden="true" />
      <div className="assistant-body">
        <BehindProvider message={message}>
          <TraceStepContext.Provider value={openStep}>
            <ReplyBody blocks={blocksFromMessage(message)} />
          </TraceStepContext.Provider>
          {summary && (
            <details className="summary-marker">
              <summary>
                Earlier messages summarised · the model continues from here with a summary of {summary.covered} messages
                {summary.focus ? ` · focus: ${summary.focus}` : ""}
              </summary>
              <Markdown text={summary.text} />
            </details>
          )}
          {message.status !== "complete" && (
            <p className={`message-status status-${message.status}`}>
              <strong>{STATUS_LABELS[message.status]}:</strong> {message.error}
            </p>
          )}
          <BehindChips message={message} actions={actions} />
          {actions?.secondOpinions?.opinions
            .filter((opinion) => opinion.message_id === message.id)
            .map((opinion) => <SecondOpinionReport key={opinion.id} opinion={opinion} actions={actions} />)}
          {actions && !onlySummary && <ReplyActions message={message} actions={actions} />}
        </BehindProvider>
      </div>
    </div>
  );
}
