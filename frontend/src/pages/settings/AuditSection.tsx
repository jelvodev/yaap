import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api, errorMessage, type Project } from "../../api/client";
import type { components } from "../../api/schema";
import { Badge, EmptyState } from "../../components/workbench/Workbench";
import { useEnvironment } from "../../environment/EnvironmentProvider";
import { type SettingsLevel, SectionHeader, formatDate } from "./shared";

type AuditLog = components["schemas"]["AuditLogOut"];
type AuditEntry = components["schemas"]["AuditEntryOut"];
type Purpose = AuditEntry["purpose"];
type DataClass = AuditEntry["data_classes"][number];

const PAGE_SIZE = 200;

const PURPOSE_LABELS: Record<Purpose, string> = {
  reply: "Reply",
  sub_run: "Sub-run",
  cli_worker: "CLI worker",
  utility: "Utility",
  a2a: "A2A agent",
  embedding: "Embedding",
  telemetry: "OpenTelemetry export",
};

const DATA_LABELS: Record<DataClass, string> = {
  conversation: "conversation",
  files: "files and tool output",
  knowledge: "knowledge",
  memory: "memory",
  attachments: "attachments",
  secrets: "secrets",
};

interface AuditFilter {
  start: string;
  end: string;
  projectId: string;
  model: string;
  purpose: Purpose | "";
}

const NO_FILTER: AuditFilter = { start: "", end: "", projectId: "", model: "", purpose: "" };

/** The filter as query values: empty fields are left out. */
function queryOf(filter: AuditFilter) {
  return {
    start: filter.start || undefined,
    end: filter.end || undefined,
    project_id: filter.projectId ? Number(filter.projectId) : undefined,
    model: filter.model || undefined,
    purpose: filter.purpose || undefined,
  };
}

function exportUrl(environmentId: number, filter: AuditFilter, format: "csv" | "json"): string {
  const params = new URLSearchParams({ format });
  for (const [key, value] of Object.entries(queryOf(filter))) {
    if (value !== undefined) params.set(key, String(value));
  }
  return `/api/v1/environments/${environmentId}/audit/export?${params.toString()}`;
}

/** The environment's audit log for a filter, a page at a time; more appends the next page. */
function useAuditLog(environmentId: number, filter: AuditFilter) {
  const [log, setLog] = useState<AuditLog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    setOffset(0);
  }, [environmentId, filter]);

  useEffect(() => {
    let current = true;
    void api
      .GET("/api/v1/environments/{environment_id}/audit", {
        params: {
          path: { environment_id: environmentId },
          query: { ...queryOf(filter), limit: PAGE_SIZE, offset },
        },
      })
      .then(({ data, error: failure }) => {
        if (!current) return;
        if (failure || !data) {
          setError(errorMessage(failure, "The audit log could not be loaded."));
          return;
        }
        setError(null);
        setLog((earlier) =>
          offset > 0 && earlier ? { ...data, entries: [...earlier.entries, ...data.entries] } : data,
        );
      });
    return () => {
      current = false;
    };
  }, [environmentId, filter, offset]);

  const more = log !== null && log.entries.length < log.total ? () => setOffset(log.entries.length) : null;
  return { log, error, more };
}

function useProjects(environmentId: number): Project[] {
  const [projects, setProjects] = useState<Project[]>([]);
  useEffect(() => {
    let current = true;
    void api
      .GET("/api/v1/environments/{environment_id}/projects", { params: { path: { environment_id: environmentId } } })
      .then(({ data }) => current && setProjects(data ?? []));
    return () => {
      current = false;
    };
  }, [environmentId]);
  return projects;
}

function Filters({
  filter,
  onChange,
  projects,
  models,
}: {
  filter: AuditFilter;
  onChange: (filter: AuditFilter) => void;
  projects: Project[];
  models: string[];
}) {
  const set = (change: Partial<AuditFilter>) => onChange({ ...filter, ...change });
  return (
    <div className="settings-audit-filters">
      <label>
        <span className="small muted">From</span>
        <input
          type="date"
          value={filter.start}
          max={filter.end || undefined}
          onChange={(event) => set({ start: event.target.value })}
        />
      </label>
      <label>
        <span className="small muted">To</span>
        <input
          type="date"
          value={filter.end}
          min={filter.start || undefined}
          onChange={(event) => set({ end: event.target.value })}
        />
      </label>
      <label>
        <span className="small muted">Project</span>
        <select value={filter.projectId} onChange={(event) => set({ projectId: event.target.value })}>
          <option value="">All</option>
          {projects.map((project) => (
            <option key={project.id} value={project.id}>
              {project.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span className="small muted">Model</span>
        <select value={filter.model} onChange={(event) => set({ model: event.target.value })}>
          <option value="">All</option>
          {models.map((model) => (
            <option key={model} value={model}>
              {model}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span className="small muted">Purpose</span>
        <select value={filter.purpose} onChange={(event) => set({ purpose: event.target.value as Purpose | "" })}>
          <option value="">All</option>
          {(Object.keys(PURPOSE_LABELS) as Purpose[]).map((purpose) => (
            <option key={purpose} value={purpose}>
              {PURPOSE_LABELS[purpose]}
            </option>
          ))}
        </select>
      </label>
      {filter !== NO_FILTER && (
        <button type="button" className="button button-ghost" onClick={() => onChange(NO_FILTER)}>
          Clear
        </button>
      )}
    </div>
  );
}

function EntryRow({ entry, linkConversation }: { entry: AuditEntry; linkConversation: boolean }) {
  const purpose = PURPOSE_LABELS[entry.purpose] + (entry.task ? `: ${entry.task}` : "");
  return (
    <tr>
      <td className="small">{formatDate(entry.time)}</td>
      <td>
        {purpose}
        {entry.outcome === "refused" && (
          <>
            {" "}
            <Badge tone="danger" title={entry.reason ?? undefined}>
              refused
            </Badge>
          </>
        )}
      </td>
      <td className="small">
        {entry.project ?? (entry.project_id !== null ? `#${entry.project_id} (removed)` : "")}
        {entry.conversation_id !== null && (
          <span className="faint">
            {" "}
            {linkConversation ? (
              <Link to={`/chat/${entry.conversation_id}`}>#{entry.conversation_id}</Link>
            ) : (
              `#${entry.conversation_id}`
            )}
          </span>
        )}
      </td>
      <td className="small">
        <span className="mono">{entry.model}</span>
        <span className="faint"> · {entry.connection}</span>
      </td>
      <td className="small">
        {entry.data_classes.map((name) => DATA_LABELS[name]).join(", ") || "—"}
        {entry.secret_names.length > 0 && <span className="faint"> ({entry.secret_names.join(", ")})</span>}
      </td>
    </tr>
  );
}

/** Environment only: every model request that left YAAP, filtered, with an export as CSV or JSON. */
export default function AuditSection({ level }: { level: Extract<SettingsLevel, { kind: "environment" }> }) {
  const environmentId = level.environment.id;
  const { active } = useEnvironment();
  const linkConversation = active?.id === environmentId;
  const [filter, setFilter] = useState<AuditFilter>(NO_FILTER);
  const { log, error, more } = useAuditLog(environmentId, filter);
  const projects = useProjects(environmentId);

  return (
    <>
      <SectionHeader
        level={level}
        title="Audit log"
        badges={log && <Badge>{log.total} requests</Badge>}
        actions={
          <>
            <a className="button button-ghost" href={exportUrl(environmentId, filter, "csv")} download>
              Export CSV
            </a>
            <a className="button button-ghost" href={exportUrl(environmentId, filter, "json")} download>
              Export JSON
            </a>
          </>
        }
        info={
          <>
            <p>
              Every model request that left YAAP from {level.environment.name}: replies, sub-runs, coding agents as
              workers, utility tasks, calls to remote A2A agents and traces sent to an OpenTelemetry collector. Each row says which model received which kinds of
              data, never the content itself. Secrets appear only by their placeholder names.
            </p>
            <p>Rows are kept for a year. The export holds the rows that match the filter.</p>
          </>
        }
      />
      <div className="wb-body wb-stack">
        <Filters filter={filter} onChange={setFilter} projects={projects} models={log?.models ?? []} />
        {error && <p className="form-error">{error}</p>}
        {log === null ? (
          <EmptyState>Loading…</EmptyState>
        ) : log.entries.length === 0 ? (
          <EmptyState>No model requests match.</EmptyState>
        ) : (
          <div className="wb-table-scroll">
            <table className="wb-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Purpose</th>
                  <th>Project and conversation</th>
                  <th>Model</th>
                  <th>Data</th>
                </tr>
              </thead>
              <tbody>
                {log.entries.map((entry) => (
                  <EntryRow key={entry.id} entry={entry} linkConversation={linkConversation} />
                ))}
              </tbody>
            </table>
          </div>
        )}
        {more && (
          <button type="button" className="button button-ghost" onClick={more}>
            Show more
          </button>
        )}
      </div>
    </>
  );
}
