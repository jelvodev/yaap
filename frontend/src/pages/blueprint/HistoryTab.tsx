import { useEffect, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";
import DiffView from "../../components/DiffView";
import { EmptyState, Panel } from "../../components/workbench/Workbench";
import "./blueprint.css";

type Commit = components["schemas"]["CommitOut"];

/** Where a file's history lives: a repository of an environment, and the path inside it. */
export interface HistoryTarget {
  environmentId: number;
  repositoryId: string;
  path: string;
  label: string;
}

function relativeTime(value: string): string {
  const seconds = Math.round((Date.now() - Date.parse(value)) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return new Date(value).toLocaleDateString();
}

/** The commits that touched one file, newest first; choosing one shows what it changed. */
export default function HistoryTab({ target, onCount }: { target: HistoryTarget; onCount?: (count: number) => void }) {
  const [commits, setCommits] = useState<Commit[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [diff, setDiff] = useState<string | null>(null);
  const path = { environment_id: target.environmentId, repository_id: target.repositoryId };

  useEffect(() => {
    let current = true;
    setCommits(null);
    setOpen(null);
    void api
      .GET("/api/v1/environments/{environment_id}/git/repositories/{repository_id}/log", {
        params: { path, query: { path: target.path || undefined, limit: 100 } },
      })
      .then(({ data, error: failure }) => {
        if (!current) return;
        if (failure || !data) setError(errorMessage(failure, "The history could not be loaded."));
        else {
          setCommits(data);
          onCount?.(data.length);
        }
      });
    return () => {
      current = false;
    };
  }, [target.environmentId, target.repositoryId, target.path]);

  async function show(hash: string) {
    if (open === hash) {
      setOpen(null);
      return;
    }
    setOpen(hash);
    setDiff(null);
    const { data, error: failure } = await api.GET(
      "/api/v1/environments/{environment_id}/git/repositories/{repository_id}/commits/{commit}",
      { params: { path: { ...path, commit: hash }, query: { path: target.path || undefined } } },
    );
    setDiff(data ? data.diff : errorMessage(failure, "This commit could not be shown."));
  }

  return (
    <Panel title="History" aside={<span className="mono">{target.label}</span>} flush>
      {error && <p className="form-error wb-empty">{error}</p>}
      {!error && commits === null && <EmptyState>Loading…</EmptyState>}
      {commits?.length === 0 && <EmptyState>No commits for this file in this layer yet.</EmptyState>}
      {commits && commits.length > 0 && (
        <ol className="bp-history">
          {commits.map((commit) => (
            <li key={commit.hash}>
              <button type="button" className="bp-history-row" aria-expanded={open === commit.hash} onClick={() => void show(commit.hash)}>
                <span className="mono bp-hash">{commit.short_hash}</span>
                <span className="bp-subject">{commit.subject}</span>
                <span className="faint small">
                  {commit.author} · <time dateTime={commit.date} title={new Date(commit.date).toLocaleString()}>{relativeTime(commit.date)}</time>
                </span>
              </button>
              {open === commit.hash && <div className="bp-history-diff">{diff === null ? <p className="faint small">Loading…</p> : <DiffView diff={diff} />}</div>}
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}
