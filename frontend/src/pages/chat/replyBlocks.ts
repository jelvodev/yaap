import { callKey } from "../../api/checkpoints";
import type { ApprovalDecision, ApprovalRequest, ToolCallResult, ToolCallStart } from "../../api/message-stream";
import { type ReplyBlock, appendLive } from "../../components/message/replyBlock";

// How each event of a streaming reply changes the blocks shown for it. Each function returns a new list.

/** A model call broke off and starts again: drop the text and reasoning it had streamed. */
export function dropStreamedText(blocks: ReplyBlock[]): ReplyBlock[] {
  let end = blocks.length;
  while (end > 0 && (blocks[end - 1].type === "text" || blocks[end - 1].type === "reasoning")) end -= 1;
  return blocks.slice(0, end);
}

export function addToolCall(blocks: ReplyBlock[], call: ToolCallStart): ReplyBlock[] {
  return [...blocks, { type: "tool_call", ...call, output: null, is_error: false }];
}

/** A delegated sub-run reports the tool it uses, on the running delegate call: the last one without output. */
export function showDelegateProgress(blocks: ReplyBlock[], label: string, step: string): ReplyBlock[] {
  let index = blocks.length - 1;
  while (index >= 0) {
    const block = blocks[index];
    if (block.type === "tool_call" && block.name === "delegate" && block.output === null) break;
    index -= 1;
  }
  if (index === -1) return blocks;
  const text = step === "started" ? `${label} is starting…` : `${label}: ${step}…`;
  return blocks.map((block, position) => (position === index ? { ...block, progress: text } : block));
}

export function addSteering(blocks: ReplyBlock[], text: string): ReplyBlock[] {
  return [...blocks, { type: "steering", text }];
}

/** Output of a running command, on the first unfinished call with the same call key. */
export function appendToolOutput(blocks: ReplyBlock[], key: string, text: string): ReplyBlock[] {
  const index = blocks.findIndex(
    (block) => block.type === "tool_call" && block.output === null && callKey(block.name, block.input) === key,
  );
  if (index === -1) return blocks;
  return blocks.map((block, position) =>
    position === index && block.type === "tool_call" ? { ...block, live: appendLive(block.live, text) } : block,
  );
}

export function setToolResult(blocks: ReplyBlock[], result: ToolCallResult): ReplyBlock[] {
  return blocks.map((block) =>
    block.type === "tool_call" && block.id === result.id
      ? { ...block, output: result.output, is_error: result.is_error }
      : block,
  );
}

export function addApproval(blocks: ReplyBlock[], request: ApprovalRequest): ReplyBlock[] {
  return [
    ...blocks,
    {
      type: "approval",
      id: request.approval_id,
      tool: request.tool,
      summary: request.summary,
      detail: request.detail,
      decision: null,
      reason: request.reason ?? null,
      suspicion: request.suspicion ?? null,
      why: request.why ?? null,
    },
  ];
}

export function resolveApproval(blocks: ReplyBlock[], approvalId: string, decision: ApprovalDecision): ReplyBlock[] {
  return blocks.map((block) => (block.type === "approval" && block.id === approvalId ? { ...block, decision } : block));
}
