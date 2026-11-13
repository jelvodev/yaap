import { useMemo } from "react";
import { useNavigate } from "react-router-dom";

import type { Project } from "../../api/client";
import { formatCost } from "../../components/trace/traceOrder";
import { boardPath, cardPath } from "../chat/useChatLinks";
import { useWaiting, type WaitingItem, type WaitingProject } from "./useWaiting";
import { ACTION_LABELS, donePercent, groupWaiting, waitingTarget, type WaitingTarget } from "./waitingLogic";
import "./waiting.css";

/** Only numbers go into the address, never text from a card or a tool call. */
function targetPath(target: WaitingTarget): string {
  if (target.kind === "card") return cardPath(target.projectId, target.cardId);
  if (target.kind === "conversation") return `/chat/${target.conversationId}`;
  return boardPath(target.projectId);
}

function rowKey(item: WaitingItem): string {
  return [item.kind, item.approval_id, item.project_id, item.card_id, item.plan_id].map((part) => part ?? "").join("-");
}

/**
 * Waiting for me: what waits for the user across the projects of this environment, by section, with one action per
 * row, and a card per project with its board on. Titles come from cards, plans and tool calls and are shown as text.
 */
export default function WaitingPage({ environmentId, projects }: { environmentId: number; projects: Project[] }) {
  const { waiting, error } = useWaiting(environmentId);
  const navigate = useNavigate();
  const names = useMemo(() => {
    const found = new Map<number, string>(projects.map((project) => [project.id, project.name]));
    for (const summary of waiting?.projects ?? []) found.set(summary.project_id, summary.name);
    return found;
  }, [projects, waiting]);
  const sections = useMemo(() => groupWaiting(waiting?.items ?? []), [waiting]);

  return (
    <section className="waiting-page">
      <h1>Waiting for me</h1>
      <p className="muted">
        Across all projects of this environment. Everything else runs or waits for something else.
      </p>
      {error && <p className="form-error">{error}</p>}
      {waiting === null && !error && <p className="muted">Loading…</p>}
      {waiting !== null && sections.length === 0 && <p className="waiting-none muted">Nothing waits for you.</p>}
      {sections.map((section) => (
        <div key={section.kind} className="waiting-section">
          <h2>
            {section.title} <span className="waiting-count">{section.items.length}</span>
          </h2>
          {section.items.map((item) => (
            <WaitingRow
              key={rowKey(item)}
              item={item}
              projectName={item.project_id !== null ? (names.get(item.project_id) ?? null) : null}
              onOpen={(target) => navigate(targetPath(target))}
            />
          ))}
        </div>
      ))}
      {waiting !== null && waiting.projects.length > 0 && (
        <div className="waiting-section">
          <h2>Projects</h2>
          <div className="waiting-projects">
            {waiting.projects.map((summary) => (
              <ProjectCard
                key={summary.project_id}
                summary={summary}
                onOpen={() => navigate(boardPath(summary.project_id))}
              />
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function WaitingRow({
  item,
  projectName,
  onOpen,
}: {
  item: WaitingItem;
  projectName: string | null;
  onOpen: (target: WaitingTarget) => void;
}) {
  const target = waitingTarget(item);
  const where = item.project_id === null ? "No project" : (projectName ?? `Project ${item.project_id}`);
  return (
    <button
      type="button"
      className="waiting-row"
      disabled={target === null}
      onClick={() => target !== null && onOpen(target)}
    >
      <span className="waiting-row-text">
        <span className="waiting-row-title">
          {item.card_id !== null && <span className="waiting-card-number">#{item.card_id}</span>}
          {item.title}
        </span>
        <span className="waiting-row-meta">{[where, item.detail].filter(Boolean).join(" · ")}</span>
      </span>
      {item.model && <span className="chip">{item.model}</span>}
      <span className={`button ${item.action === "approve" ? "button-primary" : "button-ghost"} waiting-action`}>
        {ACTION_LABELS[item.action]}
      </span>
    </button>
  );
}

function ProjectCard({ summary, onOpen }: { summary: WaitingProject; onOpen: () => void }) {
  const progress = summary.version
    ? `${summary.version} · ${summary.done} of ${summary.total} cards done`
    : "No open version";
  return (
    <button type="button" className="waiting-project" onClick={onOpen}>
      <h3>{summary.name}</h3>
      <span className="muted small">{progress}</span>
      <span className="waiting-bar" aria-hidden="true">
        <b style={{ width: `${donePercent(summary.done, summary.total)}%` }} />
      </span>
      <span className="waiting-stats">
        <span>
          <b>{summary.building}</b> building
        </span>
        <span>
          <b>{summary.blocked}</b> blocked
        </span>
        <span>
          <b>{summary.escalated}</b> escalated
        </span>
        <span>
          <b>{summary.cost_month === 0 ? "$0.00" : formatCost(summary.cost_month)}</b> this month
        </span>
      </span>
    </button>
  );
}
