import { useMemo, useState } from "react";

import type { GitRepository, Project } from "../../api/client";
import { Badge } from "../workbench/Workbench";
import ChangedFileRow from "./ChangedFileRow";
import ChangesFooter from "./ChangesFooter";
import ChangesReviewBox from "./ChangesReviewBox";
import { changesActions, syncText } from "./changesState";
import { diffPart, splitDiff } from "./diffParts";
import { useChangesReview } from "./useChangesReview";
import { useGitChanges } from "./useGitChanges";
import type { ProjectRepository } from "./useProjectRepository";
import "./changes-panel.css";

/** The changed files with their diffs in one list, and the commit, sync and review actions below it. */
function RepositoryChanges({
  project,
  repository,
  onChanged,
  onOpenReview,
  onOpenSettings,
}: {
  project: Project;
  repository: GitRepository;
  onChanged: (updated: GitRepository) => void;
  onOpenReview: (reviewId: number) => void;
  onOpenSettings?: () => void;
}) {
  const git = useGitChanges(project.environment_id, repository, onChanged);
  const review = useChangesReview(project.id);
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const parts = useMemo(() => splitDiff(git.diff ?? ""), [git.diff]);
  const actions = changesActions(repository);
  const count = repository.changes.length;

  const toggle = (path: string) =>
    setOpen((current) => {
      const next = new Set(current);
      if (!next.delete(path)) next.add(path);
      return next;
    });
  const show = (path: string) => setOpen((current) => new Set(current).add(path));

  return (
    <div className="cp">
      <div className="cp-scroll">
        <p className="cp-branch faint small">
          {repository.branch && <Badge>⎇ {repository.branch}</Badge>}
          <span>{syncText(repository)}</span>
          <span className="cp-count">{count === 0 ? "" : `${count} file${count === 1 ? "" : "s"} changed`}</span>
        </p>
        <ChangesReviewBox review={review} onShowFile={show} onOpenReport={onOpenReview} />
        {count === 0 ? (
          <p className="faint small pp-pad">No uncommitted changes.</p>
        ) : git.diff === null ? (
          <p className="faint small pp-pad">Loading diff…</p>
        ) : (
          <ul className="cp-files">
            {repository.changes.map((change) => (
              <ChangedFileRow
                key={change.path}
                change={change}
                part={diffPart(parts, change.path)}
                open={open.has(change.path)}
                onToggle={() => toggle(change.path)}
              />
            ))}
          </ul>
        )}
      </div>
      <ChangesFooter
        repository={repository}
        actions={actions}
        git={git}
        reviewStarting={review.starting}
        onCommitted={() => setOpen(new Set())}
        onReview={() => {
          git.setNotice(null);
          void review.start();
        }}
        onOpenSettings={onOpenSettings}
      />
    </div>
  );
}

/**
 * The Changes panel of the project rail: the project's uncommitted changes, committing and syncing them, and a
 * review of them. Shows why it cannot when the project's git state cannot be read (a folder that is no repository).
 */
export default function ChangesPanel({
  project,
  git,
  onOpenReview,
  onOpenSettings,
}: {
  project: Project;
  /** The project's git state, shared with the rail's count and the header chip. */
  git: ProjectRepository;
  /** Show a review's full report in Evals and reviews. */
  onOpenReview: (reviewId: number) => void;
  /** Open Project settings, where the remote is set. */
  onOpenSettings?: () => void;
}) {
  const { repository, error } = git;
  if (error) return <p className="notice pp-pad">{error}</p>;
  if (!repository) return <p className="faint small pp-pad">Loading…</p>;
  return (
    <RepositoryChanges
      key={repository.id}
      project={project}
      repository={repository}
      onChanged={git.replace}
      onOpenReview={onOpenReview}
      onOpenSettings={onOpenSettings}
    />
  );
}
