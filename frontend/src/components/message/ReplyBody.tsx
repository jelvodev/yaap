import { useState } from "react";

import { usePreferences } from "../../preferences/PreferencesProvider";
import ApprovalBlock from "../ApprovalBlock";
import { ChevronIcon } from "../icons";
import { StreamingContext } from "./contexts";
import { Markdown } from "./Markdown";
import { type ReplyBlock, groupToolCalls, thinkingHeadline } from "./replyBlock";
import { ToolCallBlock, ToolCallGroup } from "./ToolCallBlock";

export function ThinkingIndicator({ label = "Thinking…" }: { label?: string }) {
  return (
    <span className="thinking">
      <span className="shimmer">{label}</span>
      <span className="pulse-dots" aria-hidden="true">
        <span />
        <span />
        <span />
      </span>
    </span>
  );
}

function ReasoningBlock({ text, live }: { text: string; live: boolean }) {
  const [open, setOpen] = useState(false);
  const headline = live ? thinkingHeadline(text) : "";
  return (
    <>
      <button type="button" className="reasoning-toggle" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        <ChevronIcon size={13} />
        {live ? (
          <span className="shimmer reasoning-live" aria-live="polite">
            {headline ? (headline.endsWith("…") ? headline : `${headline}…`) : "Thinking…"}
          </span>
        ) : (
          "Reasoning"
        )}
      </button>
      {open && (
        <div className="reasoning">
          <Markdown text={text} />
        </div>
      )}
    </>
  );
}

interface ReplyBodyProps {
  blocks: ReplyBlock[];
  streaming?: boolean;
}

/** The blocks of a reply in order: reasoning, text, tool calls (grouped), approvals and steering messages. */
export function ReplyBody({ blocks, streaming = false }: ReplyBodyProps) {
  const last = blocks.at(-1);
  const waitingForUser = blocks.some((block) => block.type === "approval" && !block.decision);
  const runningTool = last?.type === "tool_call" && last.output === null ? last.name : null;
  // Live thinking shows its own headline; text shows itself.
  const showIndicator = streaming && !waitingForUser && (!last || (last.type !== "text" && last.type !== "reasoning"));
  const { preferences } = usePreferences();
  return (
    <StreamingContext.Provider value={streaming}>
      {groupToolCalls(blocks, preferences.verbose_tools).map((block, index) => {
        if (Array.isArray(block)) return <ToolCallGroup key={block[0].id || `group-${index}`} calls={block} />;
        switch (block.type) {
          case "reasoning":
            return <ReasoningBlock key={index} text={block.text} live={streaming && block === last} />;
          case "text":
            return <Markdown key={index} text={block.text} />;
          case "tool_call":
            return <ToolCallBlock key={block.id || index} block={block} />;
          case "approval":
            return <ApprovalBlock key={block.id} block={block} />;
          case "steering":
            return (
              <div key={index} className="steering-marker">
                <span className="steering-label">You, while YAAP worked</span>
                <span className="steering-text">{block.text}</span>
              </div>
            );
        }
      })}
      {showIndicator && <ThinkingIndicator label={runningTool ? `Running ${runningTool}…` : "Thinking…"} />}
    </StreamingContext.Provider>
  );
}
