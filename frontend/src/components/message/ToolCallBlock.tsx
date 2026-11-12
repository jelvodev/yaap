import { useContext, useEffect, useRef, useState } from "react";

import { callKey } from "../../api/checkpoints";
import { usePreferences } from "../../preferences/PreferencesProvider";
import { JsonText } from "../highlight";
import { ChevronIcon } from "../icons";
import { WhyPopover } from "../why/WhyPopover";
import { CallReasonContext, CheckpointContext, OutputContext, TraceStepContext } from "./contexts";
import { type ToolCallReplyBlock, toolLineLabel } from "./replyBlock";

const FULL_OUTPUT = /\[Full output: output (\d+),/;
const SCREENSHOT = /\[Screenshot: shot (\d+)\]/g;

/** The most telling argument of a tool call, shown next to its name. */
function inputSummary(input: Record<string, unknown>): string {
  for (const key of ["path", "command", "pattern", "url", "message", "name"]) {
    const value = input[key];
    if (typeof value === "string" && value) return value.length > 80 ? `${value.slice(0, 77)}…` : value;
  }
  return "";
}

/** The links under a finished tool call: its checkpoint (changes, restore) and its stored full output. */
function ToolCallLinks({ block, finished }: { block: ToolCallReplyBlock; finished: boolean }) {
  const checkpoints = useContext(CheckpointContext);
  const checkpointId = finished ? checkpoints?.forCall(block.id) : undefined;
  const outputConversation = useContext(OutputContext);
  const fullOutput = block.output ? FULL_OUTPUT.exec(block.output)?.[1] : undefined;
  const shots = block.output ? [...block.output.matchAll(SCREENSHOT)].map((match) => match[1]) : [];
  return (
    <>
      {checkpointId !== undefined && checkpoints && (
        <span className="tool-call-checkpoint">
          <button type="button" className="link-button" onClick={() => checkpoints.open(checkpointId, "changes")}>
            Changes
          </button>
          <button
            type="button"
            className="link-button"
            title="Put the project files back as they were before this action"
            onClick={() => checkpoints.open(checkpointId, "restore")}
          >
            Restore to before this
          </button>
        </span>
      )}
      {fullOutput && outputConversation !== null && (
        <span className="tool-call-checkpoint">
          <a
            className="link-button"
            href={`/api/v1/conversations/${outputConversation}/outputs/${fullOutput}`}
            target="_blank"
            rel="noreferrer"
            title="The model got the start and end of this output; open all of it"
          >
            Full output
          </a>
        </span>
      )}
      {shots.length > 0 && outputConversation !== null && (
        <div className="tool-call-shots">
          {shots.map((shot) => {
            const url = `/api/v1/conversations/${outputConversation}/browser/shots/${shot}`;
            return (
              <a key={shot} href={url} target="_blank" rel="noreferrer" title="Open the screenshot">
                <img src={url} alt={`Browser screenshot ${shot}`} loading="lazy" />
              </a>
            );
          })}
        </div>
      )}
    </>
  );
}

/** The input and output of a tool call, shown when its line is opened, with a link to its step in the trace. */
function ToolCallDetails({ block }: { block: ToolCallReplyBlock }) {
  const openStep = useContext(TraceStepContext);
  // A running call has no step yet; an imported one was never traced by YAAP.
  const traced = openStep !== null && block.output !== null && !block.imported && block.id !== "";
  return (
    <div className="tool-call-body">
      <div className="tool-call-label">Input</div>
      <pre className="hljs">
        <JsonText text={JSON.stringify(block.input, null, 2)} />
      </pre>
      {block.output !== null && (
        <>
          <div className="tool-call-label">{block.is_error ? "Error" : "Output"}</div>
          <pre className="hljs">{block.output ? <JsonText text={block.output} /> : "(empty)"}</pre>
        </>
      )}
      {traced && (
        <button
          type="button"
          className="link-button tool-call-trace"
          title="This call in the reply's trace: its duration, approval and output"
          onClick={() => openStep(block.id)}
        >
          Show in trace
        </button>
      )}
    </div>
  );
}

/** One tool call of a reply: its line with name and state, its links, the live output while it runs, and details. */
export function ToolCallBlock({ block }: { block: ToolCallReplyBlock }) {
  const { preferences } = usePreferences();
  const [open, setOpen] = useState(preferences.verbose_tools);
  const state = block.output === null ? "running" : block.is_error ? "error" : "done";
  const summary = inputSummary(block.input);
  const reason = useContext(CallReasonContext)(callKey(block.name, block.input))?.why;
  const liveRef = useRef<HTMLPreElement>(null);
  useEffect(() => {
    if (liveRef.current) liveRef.current.scrollTop = liveRef.current.scrollHeight;
  }, [block.live]);
  return (
    <div className={`tool-call tool-call-${state}`}>
      <button type="button" className="tool-call-head" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        <ChevronIcon size={12} />
        <span className="tool-call-name">{block.name}</span>
        {summary && <span className="tool-call-summary">{summary}</span>}
        <span className="tool-call-state">
          {state === "running" ? (block.progress ?? "Running…") : state === "error" ? "Failed" : ""}
          {block.imported && (
            <span className="faint" title="From the imported session: shown as it was, not run by YAAP">
              {state === "error" ? " · imported" : "Imported"}
            </span>
          )}
        </span>
      </button>
      {reason && <WhyPopover why={reason} />}
      <ToolCallLinks block={block} finished={state !== "running"} />
      {state === "running" && block.live && (
        <pre className="tool-call-live" ref={liveRef}>
          {block.live}
        </pre>
      )}
      {open && <ToolCallDetails block={block} />}
    </div>
  );
}

/** Three or more tool calls in a row, folded into one line until opened (`list_files · 4 × write_file`). */
export function ToolCallGroup({ calls }: { calls: ToolCallReplyBlock[] }) {
  const [open, setOpen] = useState(false);
  const running = calls.some((call) => call.output === null);
  const failed = calls.filter((call) => call.is_error).length;
  return (
    <div className="tool-group">
      <button type="button" className="tool-group-head" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        <ChevronIcon size={12} />
        <span className="tool-group-names" title={`${calls.length} tool calls`}>
          {running ? "Using " : ""}
          {toolLineLabel(calls)}
        </span>
        {failed > 0 && <span className="tool-call-state">{failed} failed</span>}
      </button>
      {open && (
        <div className="tool-group-body">
          {calls.map((call, index) => (
            <ToolCallBlock key={call.id || index} block={call} />
          ))}
        </div>
      )}
    </div>
  );
}
