import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../../api/client";
import type { components } from "../../api/schema";
import { RUN_STATUS_LABELS, WORKER_LABELS } from "../../components/trace/runLabels";
import TraceDialog from "../../components/trace/TraceDialog";
import type { TraceTarget } from "../../components/trace/useTrace";
import { Badge, EmptyState, FieldRow, Panel } from "../../components/workbench/Workbench";
import { type Run, useAgentRuns } from "./useAgentRuns";

export type Agent = components["schemas"]["AgentOut"];
export type AgentTemplate = components["schemas"]["AgentTemplateOut"];
type AgentTool = components["schemas"]["AgentToolOut"];

let toolCache: Promise<AgentTool[]> | null = null;

function loadTools(): Promise<AgentTool[]> {
  toolCache ??= api.GET("/api/v1/agent-tools").then(({ data }) => data ?? []);
  return toolCache;
}

/** The agent's preset, tools (grouped into reading and changing), skills and commands. */
export function AgentSetup({ agent }: { agent: Agent }) {
  const [tools, setTools] = useState<AgentTool[]>([]);
  useEffect(() => {
    void loadTools().then(setTools);
  }, []);

  const given = agent.tools === null ? tools : tools.filter((tool) => agent.tools?.includes(tool.name));
  const reading = given.filter((tool) => tool.read_only);
  const changing = given.filter((tool) => !tool.read_only);
  const unknown = (agent.tools ?? []).filter((name) => !tools.some((tool) => tool.name === name));
  const readOnly = agent.tools !== null && changing.length === 0;

  return (
    <div className="wb-grid-2">
      <Panel title="Setup" flush>
        <div>
          <FieldRow label="Description">{agent.description || <span className="faint">None</span>}</FieldRow>
          <FieldRow label="Model" hint={agent.role ? "a model role" : "a preset"}>
            {agent.role ? <Badge tone="accent">{agent.role}</Badge> : (agent.preset ?? <span className="faint">The conversation's own</span>)}
          </FieldRow>
          <FieldRow label="Skills">{agent.skills.length ? agent.skills.join(", ") : <span className="faint">None pinned</span>}</FieldRow>
          <FieldRow label="Commands">
            {agent.commands === null ? <span className="faint">Every command</span> : agent.commands.join(", ") || <span className="faint">None</span>}
          </FieldRow>
          <FieldRow label="Defined in">
            <span className="mono small">{agent.path}</span> <span className="faint small">· {agent.source_label}</span>
          </FieldRow>
        </div>
      </Panel>
      <Panel
        title="Tools"
        aside={
          agent.tools === null ? (
            <Badge>every tool of the conversation</Badge>
          ) : readOnly ? (
            <Badge tone="ok">read-only</Badge>
          ) : (
            <Badge tone="warn">can change things</Badge>
          )
        }
      >
        <ToolGroup title="Reads" tools={reading} />
        <ToolGroup title="Changes or reaches out" tools={changing} />
        {unknown.length > 0 && (
          <p className="form-error small">Unknown tools in the definition: {unknown.join(", ")}</p>
        )}
        <p className="faint small">Change the tools in the definition below (`tools:` in the front matter).</p>
      </Panel>
      {agent.warnings.map((warning) => (
        <p key={warning} className="form-error">
          {warning}
        </p>
      ))}
    </div>
  );
}

function ToolGroup({ title, tools }: { title: string; tools: AgentTool[] }) {
  if (tools.length === 0) return null;
  return (
    <div className="bp-tool-group">
      <div className="bp-tool-group-title">{title}</div>
      {tools.map((tool) => (
        <span key={tool.name} className="bp-tool mono" title={tool.description}>
          {tool.name}
          {tool.permission === "ask" && <span className="faint"> · asks</span>}
        </span>
      ))}
    </div>
  );
}

function duration(run: Run): string {
  if (!run.finished_at) return "";
  const seconds = Math.max(0, Math.round((Date.parse(run.finished_at) - Date.parse(run.started_at)) / 1000));
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

/**
 * The runs of one agent in an environment (or every reply's run without an agent), newest first, a page at a time,
 * kept current while one runs. A run's sub-runs and steps are in its trace.
 */
export function AgentRuns({ environmentId, agent, onCount }: { environmentId: number; agent?: string; onCount?: (count: number) => void }) {
  const { runs, more, loadingMore, loadMore } = useAgentRuns(environmentId, agent);
  const [trace, setTrace] = useState<TraceTarget | null>(null);

  useEffect(() => {
    if (runs) onCount?.(runs.length);
  }, [runs?.length]);

  if (runs === null) return <EmptyState>Loading…</EmptyState>;
  return (
    <Panel title="Runs" aside={<span>newest first; open a run's trace for its steps and sub-runs</span>} flush>
      {runs.length === 0 ? (
        <EmptyState>No runs yet. Choose this agent next to the message box to start one.</EmptyState>
      ) : (
        <div className="wb-table-scroll">
          <table className="wb-table runs-table">
            <thead>
              <tr>
                <th>Conversation</th>
                <th>Agent</th>
                <th>Status</th>
                <th className="wb-num">Tools</th>
                <th>Approvals</th>
                <th>Started</th>
                <th>Took</th>
                <th>
                  <span className="visually-hidden">Trace</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <RunRow
                  key={run.id}
                  run={run}
                  onShowTrace={() => setTrace({ conversationId: run.conversation_id, runId: run.id })}
                />
              ))}
            </tbody>
          </table>
          {more && (
            <div className="runs-more">
              <button
                type="button"
                className="button button-ghost"
                disabled={loadingMore}
                onClick={() => void loadMore()}
              >
                {loadingMore ? "Loading…" : "Load more"}
              </button>
            </div>
          )}
        </div>
      )}
      {trace && <TraceDialog target={trace} onClose={() => setTrace(null)} />}
    </Panel>
  );
}

function RunRow({ run, onShowTrace }: { run: Run; onShowTrace: () => void }) {
  const delegated = run.parent_run_id != null;
  return (
    <tr>
      <td>
        <Link to={`/chat/${run.conversation_id}`}>{run.conversation_title}</Link>
        {run.project_name && <span className="faint small"> · {run.project_name}</span>}
        {delegated && run.task && (
          <div className="faint small">
            Delegated: {run.task}
          </div>
        )}
      </td>
      <td>
        {run.agent ?? <span className="faint">{delegated ? "worker" : "YAAP"}</span>}
        {delegated && run.worker ? <div className="small">{WORKER_LABELS[run.worker] ?? run.worker}</div> : null}
        {delegated ? <div className="faint small mono">{run.model}</div> : null}
      </td>
      <td>
        <span className={`run-status run-${run.status}`}>{RUN_STATUS_LABELS[run.status] ?? run.status}</span>
      </td>
      <td className="wb-num">{run.tool_calls}</td>
      <td>
        {run.approvals.length === 0 ? (
          <span className="faint">–</span>
        ) : (
          <details>
            <summary>
              {run.approvals.filter((approval) => approval.decision === "approve").length} of {run.approvals.length}{" "}
              approved
            </summary>
            <ul className="approval-log">
              {run.approvals.map((approval) => (
                <li key={approval.decided_at}>
                  <strong>{approval.decision === "approve" ? "Approved" : "Declined"}</strong> {approval.summary}
                </li>
              ))}
            </ul>
          </details>
        )}
      </td>
      <td className="nowrap">{new Date(run.started_at).toLocaleString()}</td>
      <td className="nowrap">{duration(run)}</td>
      <td>
        <button type="button" className="button button-ghost" onClick={onShowTrace}>
          Trace
        </button>
      </td>
    </tr>
  );
}
