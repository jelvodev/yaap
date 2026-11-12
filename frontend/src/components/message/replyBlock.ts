import type { Message, Why } from "../../api/client";
import type { ApprovalDecision } from "../../api/message-stream";

/** One piece of an assistant reply, in the order it happened. */
export type ReplyBlock =
  | { type: "reasoning"; text: string }
  | { type: "text"; text: string }
  | {
      type: "tool_call";
      id: string;
      name: string;
      input: Record<string, unknown>;
      output: string | null;
      is_error: boolean;
      // While a delegate call runs: who works on it and the tool it is using now.
      progress?: string;
      // While a command runs: the newest part of its output.
      live?: string;
      // From an imported Claude Code or Codex session: shown as it was, never run by YAAP.
      imported?: boolean;
    }
  // A message the user sent while the agent worked; the agent got it at this point.
  | { type: "steering"; text: string }
  | {
      type: "approval";
      id: string;
      tool: string;
      summary: string;
      detail: string;
      decision: ApprovalDecision | null;
      reason?: string | null;
      suspicion?: string | null;
      why?: Why | null;
    };

export type ToolCallReplyBlock = Extract<ReplyBlock, { type: "tool_call" }>;

const LIVE_TAIL = 20_000;

export function appendLive(live: string | undefined, text: string): string {
  const joined = (live ?? "") + text;
  return joined.length > LIVE_TAIL ? joined.slice(-LIVE_TAIL) : joined;
}

export function blocksFromMessage(message: Message): ReplyBlock[] {
  return message.content.flatMap((block): ReplyBlock[] => {
    if (block.type === "steering") return [{ type: "steering", text: block.text ?? "" }];
    if (block.type !== "text" && block.type !== "reasoning" && block.type !== "tool_call") return [];
    if (block.type === "tool_call") {
      return [
        {
          type: "tool_call",
          id: block.id ?? "",
          name: block.name ?? "tool",
          input: block.input ?? {},
          output: block.output ?? null,
          is_error: block.is_error,
          imported: block.imported ?? false,
        },
      ];
    }
    return [{ type: block.type, text: block.text }];
  });
}

/** Add streamed text to the reply, extending the last block when it is of the same kind. */
export function appendText(blocks: ReplyBlock[], type: "reasoning" | "text", text: string): ReplyBlock[] {
  const last = blocks.at(-1);
  if (last && last.type === type) return [...blocks.slice(0, -1), { type, text: last.text + text }];
  return [...blocks, { type, text }];
}

/** What the model is working on now: the latest sentence of its thinking summary, short and without "I'm". */
export function thinkingHeadline(text: string): string {
  const sentences = text
    .replace(/\s+/g, " ")
    .split(/(?<=[.!?])\s+/)
    .map((sentence) => sentence.trim())
    .filter(Boolean);
  if (sentences.length === 0) return "";
  // The last sentence may still be arriving; show the one before it until it ends.
  const complete = /[.!?]$/.test(sentences.at(-1) ?? "");
  let line = (complete || sentences.length === 1 ? sentences.at(-1) : sentences.at(-2)) ?? "";
  line = line.replace(/^(I'm|I am|I'll|I will|I've|I have|Now I'm|Now I am|Let me)\s+/i, "").replace(/[.!?]+$/, "");
  line = line.charAt(0).toUpperCase() + line.slice(1);
  return line.length > 90 ? `${line.slice(0, 87).trimEnd()}…` : line;
}

/** The reply's blocks, with runs of three or more tool calls grouped unless tool calls are shown in full. */
export function groupToolCalls(blocks: ReplyBlock[], verbose: boolean): (ReplyBlock | ToolCallReplyBlock[])[] {
  const result: (ReplyBlock | ToolCallReplyBlock[])[] = [];
  let run: ToolCallReplyBlock[] = [];
  const flush = () => {
    if (run.length >= 3 && !verbose) result.push(run);
    else result.push(...run);
    run = [];
  };
  for (const block of blocks) {
    if (block.type === "tool_call") run.push(block);
    else {
      flush();
      result.push(block);
    }
  }
  flush();
  return result;
}

/** A run of tool calls as one line, each tool once in the order first used:
 * `list_files · run_command · 4 × write_file`. */
export function toolLineLabel(calls: { name: string }[]): string {
  const counts = new Map<string, number>();
  for (const call of calls) counts.set(call.name, (counts.get(call.name) ?? 0) + 1);
  return [...counts].map(([name, count]) => (count > 1 ? `${count} × ${name}` : name)).join(" · ");
}
