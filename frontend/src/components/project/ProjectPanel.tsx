import type { Project } from "../../api/client";
import HistoryTab from "../../pages/blueprint/HistoryTab";
import { type PanelTab, locationFromLink, railLocation } from "../../pages/chat/railPanels";
import { Badge } from "../workbench/Workbench";
import BranchesTab from "./BranchesTab";
import ChangesPanel from "./ChangesPanel";
import { syncText } from "./changesState";
import CheckpointsTab, { type CheckpointFocus } from "./CheckpointsTab";
import EvalsTab from "./EvalsTab";
import FilesTab from "./FilesTab";
import KnowledgeTab from "./KnowledgeTab";
import LearningTab from "./LearningTab";
import MemoryTab from "./MemoryTab";
import ReviewsTab from "./ReviewsTab";
import TasksTab from "./TasksTab";
import type { LearningProposals } from "./useLearning";
import type { ProjectRepository } from "./useProjectRepository";
import ProjectSettings from "./ProjectSettings";
import RailPanelHead from "./RailPanelHead";
import "./project-panel.css";

/**
 * A panel of the project rail beside the chat: changes with commit, files, branches, history and checkpoints,
 * memory and learning, knowledge, tasks, evals and reviews, and the project's settings. `tab` names the panel and
 * the view of its switch (the old tab values, `railPanels.ts`).
 */
export default function ProjectPanel({
  project,
  tab,
  onTabChange,
  onClose,
  refreshKey,
  checkpointFocus = null,
  reviewFocus = null,
  onReviewStarted,
  onCheckpointRestored,
  onProjectChanged,
  onProjectRemoved,
  onOpenDocument,
  git,
  learning,
}: {
  project: Project;
  tab: PanelTab;
  onTabChange: (tab: PanelTab) => void;
  onClose: () => void;
  /** Changes when a reply may have changed files, so the git state is read again. */
  refreshKey: unknown;
  /** A checkpoint to show, chosen from a tool call in the chat. */
  checkpointFocus?: CheckpointFocus | null;
  /** A review to show, started from the changes or the conversation. */
  reviewFocus?: number | null;
  onReviewStarted: (reviewId: number) => void;
  onCheckpointRestored?: () => void;
  onProjectChanged: (project: Project) => void;
  onProjectRemoved: () => void;
  /** Open a Markdown file beside the chat as the conversation's document. */
  onOpenDocument?: (path: string) => void;
  /** The project's git state, read by the rail (`useProjectRail`) and shared with its counts. */
  git: ProjectRepository;
  /** The open learning proposals, shared with the rail's count. */
  learning: LearningProposals;
}) {
  const { repository, error } = git;
  const repositoryId = `project-${project.id}`;

  const count = repository?.changes.length ?? 0;
  const location = locationFromLink(tab) ?? railLocation("changes");

  return (
    <aside className="project-panel" aria-label={`Project ${project.name}`}>
      <RailPanelHead
        location={location}
        onView={onTabChange}
        onClose={onClose}
        extras={
          <>
            {tab === "changes" && count > 0 && <Badge>{count === 1 ? "1 file" : `${count} files`}</Badge>}
            {tab === "branches" && repository?.branch && <Badge>⎇ {repository.branch}</Badge>}
            {tab === "branches" && repository && !repository.error && (
              <span className="faint small">{syncText(repository)}</span>
            )}
          </>
        }
      />
      <div className="pp-body">
        {(tab === "branches" || tab === "history") && error && <p className="notice pp-pad">{error}</p>}
        {tab === "changes" && (
          <ChangesPanel
            project={project}
            git={git}
            onOpenReview={onReviewStarted}
            onOpenSettings={() => onTabChange("project")}
          />
        )}
        {tab === "files" && (
          <FilesTab key={project.id} projectId={project.id} refreshKey={refreshKey} onOpenDocument={onOpenDocument} />
        )}
        {tab === "branches" && repository && !repository.error && (
          <BranchesTab environmentId={project.environment_id} repositoryId={repositoryId} onRepositoryChanged={git.replace} />
        )}
        {tab === "history" && repository && !repository.error && (
          <div className="pp-pad">
            <HistoryTab
              key={`${repository.id}-${repository.ahead}-${count}`}
              target={{ environmentId: project.environment_id, repositoryId, path: "", label: repository.branch ?? "" }}
            />
          </div>
        )}
        {tab === "checkpoints" && (
          <CheckpointsTab
            projectId={project.id}
            focus={checkpointFocus}
            refreshKey={refreshKey}
            onRestored={() => {
              void git.reload();
              onCheckpointRestored?.();
            }}
          />
        )}
        {tab === "memory" && <MemoryTab projectId={project.id} />}
        {tab === "knowledge" && <KnowledgeTab projectId={project.id} />}
        {tab === "tasks" && <TasksTab projectId={project.id} environmentId={project.environment_id} />}
        {tab === "evals" && <EvalsTab key={project.id} projectId={project.id} />}
        {tab === "learning" && <LearningTab key={project.id} projectId={project.id} proposals={learning} />}
        {tab === "reviews" && <ReviewsTab key={project.id} projectId={project.id} focus={reviewFocus} />}
        {tab === "project" && (
          <ProjectSettings project={project} onChanged={onProjectChanged} onRemoved={onProjectRemoved} git={git} />
        )}
      </div>
    </aside>
  );
}
