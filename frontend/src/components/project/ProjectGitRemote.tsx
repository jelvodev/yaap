import { useState } from "react";

import { type GitRepository, api, errorMessage } from "../../api/client";
import type { ProjectRepository } from "./useProjectRepository";

/** The project's git remote (origin) and the form to change it. */
function RemoteForm({
  environmentId,
  repository,
  onSaved,
  onCancel,
}: {
  environmentId: number;
  repository: GitRepository;
  onSaved: (updated: GitRepository) => void;
  onCancel: () => void;
}) {
  const [url, setUrl] = useState(repository.remote_url ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setSaving(true);
    setError(null);
    const { data, error: failure } = await api.PUT(
      "/api/v1/environments/{environment_id}/git/repositories/{repository_id}/remote",
      { params: { path: { environment_id: environmentId, repository_id: repository.id } }, body: { url: url.trim() } },
    );
    setSaving(false);
    if (data) onSaved(data);
    else setError(errorMessage(failure));
  }

  return (
    <>
      <form
        className="pp-row"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <input
          aria-label="GitHub URL"
          value={url}
          placeholder="https://github.com/owner/repository.git"
          onChange={(event) => setUrl(event.target.value)}
        />
        <button type="submit" className="button" disabled={!url.trim() || saving}>
          {saving ? "Saving…" : "Save"}
        </button>
        <button type="button" className="button button-ghost" onClick={onCancel}>
          Cancel
        </button>
      </form>
      {error && <p className="form-error small">{error}</p>}
    </>
  );
}

/** Project settings' Git group: where the project pushes to and pulls from. */
export default function ProjectGitRemote({ environmentId, git }: { environmentId: number; git: ProjectRepository }) {
  const [editing, setEditing] = useState(false);
  const [saved, setSaved] = useState(false);
  const { repository, error } = git;

  if (error) return <p className="faint small">{error}</p>;
  if (!repository) return <p className="faint small">Loading…</p>;
  return (
    <div className="pp-git-remote">
      <div className="pp-git-remote-row">
        <div className="pp-git-remote-text">
          <span className="pp-label">Remote</span>
          {repository.remote_url ? (
            <span className="mono small pp-git-remote-url">{repository.remote_url}</span>
          ) : (
            <span className="faint small">No remote set; Sync under Changes needs one.</span>
          )}
        </div>
        {!editing && (
          <button
            type="button"
            className="button"
            onClick={() => {
              setEditing(true);
              setSaved(false);
            }}
          >
            {repository.remote_url ? "Change" : "Set remote"}
          </button>
        )}
      </div>
      {editing && (
        <RemoteForm
          environmentId={environmentId}
          repository={repository}
          onSaved={(updated) => {
            git.replace(updated);
            setEditing(false);
            setSaved(true);
          }}
          onCancel={() => setEditing(false)}
        />
      )}
      {saved && <p className="muted small">Remote saved.</p>}
    </div>
  );
}
