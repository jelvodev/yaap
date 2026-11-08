import { type FormEvent, useState } from "react";

import type { GitRepository } from "../../api/client";
import { SparkleIcon } from "../icons";
import { type ChangesActions, commitLabel, discardQuestion } from "./changesState";
import DiscardConfirm from "./DiscardConfirm";
import SyncMenu from "./SyncMenu";
import type { GitChanges, SyncAction } from "./useGitChanges";

/** The Changes panel's sticky bottom: the commit message with Suggest, *Commit N files*, Sync, Review and Discard all. */
export default function ChangesFooter({
  repository,
  actions,
  git,
  reviewStarting,
  onCommitted,
  onReview,
  onOpenSettings,
}: {
  repository: GitRepository;
  actions: ChangesActions;
  git: GitChanges;
  reviewStarting: boolean;
  onCommitted: () => void;
  onReview: () => void;
  /** Open Project settings, where the remote is set. */
  onOpenSettings?: () => void;
}) {
  const [message, setMessage] = useState("");
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const { busy, notice } = git;
  const working = Boolean(busy) || reviewStarting;
  const syncBusy: SyncAction | null = busy === "fetch" || busy === "pull" || busy === "push" ? busy : null;

  async function suggest() {
    const proposed = await git.suggestMessage();
    if (proposed !== null) setMessage(proposed);
  }

  async function discardAll() {
    await git.discard(null);
    setConfirmDiscard(false);
  }

  async function commit(event: FormEvent) {
    event.preventDefault();
    if (await git.commit(message)) {
      setMessage("");
      onCommitted();
    }
  }

  return (
    <div className="cp-footer">
      {actions.commit && (
        <div className="cp-message">
          <label className="visually-hidden" htmlFor="pp-commit-message">
            Commit message
          </label>
          <textarea
            id="pp-commit-message"
            form="cp-commit-form"
            value={message}
            rows={Math.min(6, Math.max(2, message.split("\n").length))}
            placeholder="Commit message (English)"
            onChange={(event) => setMessage(event.target.value)}
          />
          <button
            type="button"
            className="icon-button cp-suggest"
            disabled={working}
            aria-label="Suggest a commit message"
            title="Let the utility model propose a message from the changes"
            onClick={() => void suggest()}
          >
            {busy === "suggest" ? <span className="running-dot" aria-hidden="true" /> : <SparkleIcon size={14} />}
          </button>
        </div>
      )}
      <form id="cp-commit-form" className="cp-buttons" onSubmit={(event) => void commit(event)}>
        {actions.commit && (
          <button type="submit" className="button button-primary pp-grow" disabled={!message.trim() || working}>
            {busy === "commit" ? "Committing…" : commitLabel(repository.changes.length)}
          </button>
        )}
        {actions.sync !== "hidden" && (
          <SyncMenu
            repository={repository}
            actions={actions}
            busy={syncBusy}
            disabled={working}
            onSync={(action) => void git.sync(action)}
          />
        )}
      </form>
      {actions.remoteHint && (
        <p className="faint small">
          No remote set, so there is nothing to sync with.{" "}
          {onOpenSettings && (
            <button type="button" className="link-button" onClick={onOpenSettings}>
              Set one in Project settings
            </button>
          )}
        </p>
      )}
      {(actions.review || actions.discard) && (
        <div className="cp-links">
          {actions.review && (
            <button
              type="button"
              className="link-button small"
              disabled={working}
              title="Let a model of another vendor than the one that wrote the code review these changes"
              onClick={onReview}
            >
              {reviewStarting ? "Starting the review…" : "Review these changes"}
            </button>
          )}
          {actions.discard && (
            <button
              type="button"
              className="link-button small"
              disabled={working}
              title="Discard every uncommitted change, after a checkpoint"
              onClick={() => setConfirmDiscard(true)}
            >
              Discard all…
            </button>
          )}
        </div>
      )}
      {actions.discard && confirmDiscard && (
        <DiscardConfirm
          question={discardQuestion({ all: repository.changes.length })}
          busy={busy === "discard"}
          onCancel={() => setConfirmDiscard(false)}
          onDiscard={() => void discardAll()}
        />
      )}
      {notice && <p className={notice.error ? "form-error small" : "muted small"}>{notice.text}</p>}
    </div>
  );
}
