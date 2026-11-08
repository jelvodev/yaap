import { type FormEvent, useCallback, useEffect, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";
import DiffView from "../DiffView";
import { Badge } from "../workbench/Workbench";
import { useConfirm } from "../dialog/ConfirmDialog";

type Branches = components["schemas"]["BranchesOut"];
type Pulls = components["schemas"]["PullsOut"];
type PullDetail = components["schemas"]["PullDetailOut"];
type Repository = components["schemas"]["RepositoryOut"];
type MergeMethod = "squash" | "merge" | "rebase";

type Notice = { error: boolean; text: string } | null;

/** Branches and pull requests of a project. */
export default function BranchesTab({
  environmentId,
  repositoryId,
  onRepositoryChanged,
}: {
  environmentId: number;
  repositoryId: string;
  onRepositoryChanged: (repository: Repository) => void;
}) {
  const confirm = useConfirm();
  const [branches, setBranches] = useState<Branches | null>(null);
  const [name, setName] = useState("");
  const [notice, setNotice] = useState<Notice>(null);
  const path = { params: { path: { environment_id: environmentId, repository_id: repositoryId } } };

  const load = useCallback(async () => {
    const { data, error } = await api.GET("/api/v1/environments/{environment_id}/git/repositories/{repository_id}/branches", {
      params: { path: { environment_id: environmentId, repository_id: repositoryId } },
    });
    if (data) setBranches(data);
    else setNotice({ error: true, text: errorMessage(error, "The branches could not be read.") });
  }, [environmentId, repositoryId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(
    request: () => Promise<{ data?: Branches; error?: unknown }>,
    done: string,
  ): Promise<boolean> {
    setNotice(null);
    const { data, error } = await request();
    if (error || !data) {
      setNotice({ error: true, text: errorMessage(error) });
      return false;
    }
    setBranches(data);
    onRepositoryChanged(data.repository);
    setNotice({ error: false, text: done });
    return true;
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    const branch = name.trim();
    const ok = await act(
      () => api.POST("/api/v1/environments/{environment_id}/git/repositories/{repository_id}/branches", { ...path, body: { name: branch, force: false } }),
      `On the new branch ${branch}. Uncommitted changes came along.`,
    );
    if (ok) setName("");
  }

  function switchTo(branch: string) {
    void act(
      () =>
        api.POST("/api/v1/environments/{environment_id}/git/repositories/{repository_id}/branches/switch", {
          ...path,
          body: { name: branch, force: false },
        }),
      `Switched to ${branch}.`,
    );
  }

  async function remove(branch: string) {
    if (!(await confirm({ message: `Delete the local branch ${branch}?`, confirmLabel: "Delete", tone: "danger" }))) return;
    const url = "/api/v1/environments/{environment_id}/git/repositories/{repository_id}/branches/delete" as const;
    const { data, error } = await api.POST(url, { ...path, body: { name: branch, force: false } });
    if (data) {
      setBranches(data);
      setNotice({ error: false, text: `${branch} deleted.` });
      return;
    }
    const text = errorMessage(error);
    const anyway = { message: `${text}\n\nDelete ${branch} anyway?`, confirmLabel: "Delete anyway", tone: "danger" } as const;
    if (text.includes("not merged") && (await confirm(anyway))) {
      await act(() => api.POST(url, { ...path, body: { name: branch, force: true } }), `${branch} deleted.`);
      return;
    }
    setNotice({ error: true, text });
  }

  return (
    <div className="pp-pad pp-branches">
      <section className="pp-section">
        <h3>Branches</h3>
        {branches === null ? (
          <p className="faint small">Loading…</p>
        ) : (
          <ul className="pp-branch-list">
            {branches.branches.map((branch) => (
              <li key={branch.name}>
                <span className="mono">{branch.name}</span>
                {branch.current && <Badge tone="ok">current</Badge>}
                {branch.track && <span className="faint small">{branch.track}</span>}
                <span className="faint small pp-branch-subject">{branch.subject}</span>
                {!branch.current && (
                  <span className="pp-branch-actions">
                    <button type="button" className="link-button" onClick={() => switchTo(branch.name)}>
                      Switch
                    </button>
                    <button type="button" className="link-button danger-link" onClick={() => void remove(branch.name)}>
                      Delete
                    </button>
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
        <form className="pp-row" onSubmit={(event) => void create(event)}>
          <input
            aria-label="New branch name"
            className="mono"
            placeholder="feature/login"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
          <button type="submit" className="button" disabled={!name.trim()}>
            New branch
          </button>
        </form>
        {notice && <p className={`small${notice.error ? " form-error" : ""}`}>{notice.text}</p>}
      </section>
      <PullRequests
        environmentId={environmentId}
        repositoryId={repositoryId}
        currentBranch={branches?.repository.branch ?? null}
        refresh={branches}
      />
    </div>
  );
}

function PullRequests({
  environmentId,
  repositoryId,
  currentBranch,
  refresh,
}: {
  environmentId: number;
  repositoryId: string;
  currentBranch: string | null;
  refresh: unknown;
}) {
  const confirm = useConfirm();
  const [pulls, setPulls] = useState<Pulls | null>(null);
  const [state, setState] = useState<"open" | "closed">("open");
  const [open, setOpen] = useState<PullDetail | null>(null);
  const [draft, setDraft] = useState<{ title: string; body: string; base: string; isDraft: boolean } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const pathParams = { environment_id: environmentId, repository_id: repositoryId };

  const load = useCallback(async () => {
    const { data, error } = await api.GET("/api/v1/environments/{environment_id}/git/repositories/{repository_id}/pulls", {
      params: { path: { environment_id: environmentId, repository_id: repositoryId }, query: { state } },
    });
    if (data) setPulls(data);
    else setNotice({ error: true, text: errorMessage(error, "The pull requests could not be read.") });
  }, [environmentId, repositoryId, state]);

  useEffect(() => {
    void load();
  }, [load, refresh]);

  async function show(number: number) {
    if (open?.number === number) {
      setOpen(null);
      return;
    }
    const { data, error } = await api.GET(
      "/api/v1/environments/{environment_id}/git/repositories/{repository_id}/pulls/{number}",
      { params: { path: { ...pathParams, number } } },
    );
    if (data) setOpen(data);
    else setNotice({ error: true, text: errorMessage(error) });
  }

  async function propose() {
    if (!draft) return;
    setBusy("propose");
    const { data, error } = await api.POST(
      "/api/v1/environments/{environment_id}/git/repositories/{repository_id}/pulls/proposal",
      { params: { path: pathParams }, body: { base: draft.base || null } },
    );
    setBusy(null);
    if (data) setDraft({ ...draft, title: data.title, body: data.body });
    else setNotice({ error: true, text: errorMessage(error, "No proposal could be made.") });
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    if (!draft) return;
    const base = draft.base || pulls?.default_branch || "the default branch";
    const question = `Open a pull request from ${currentBranch} into ${base}? The branch is pushed first when GitHub does not have it all yet.`;
    if (!(await confirm({ title: "Open pull request", message: question, confirmLabel: "Open pull request" }))) return;
    setBusy("create");
    const { data, error } = await api.POST("/api/v1/environments/{environment_id}/git/repositories/{repository_id}/pulls", {
      params: { path: pathParams },
      body: { title: draft.title.trim(), body: draft.body, base: draft.base || null, draft: draft.isDraft },
    });
    setBusy(null);
    if (error || !data) {
      setNotice({ error: true, text: errorMessage(error, "The pull request could not be opened.") });
      return;
    }
    setDraft(null);
    setNotice({ error: false, text: `Pull request #${data.number} opened.` });
    await load();
  }

  async function merge(pull: PullDetail, method: MergeMethod, deleteBranch: boolean) {
    const how = { squash: "Squash and merge", merge: "Merge commit", rebase: "Rebase and merge" }[method];
    const then = deleteBranch ? ` Then delete ${pull.head} on GitHub.` : "";
    if (!(await confirm({ title: how, message: `${how} #${pull.number} "${pull.title}" into ${pull.base}?${then}`, confirmLabel: how }))) return;
    setBusy("merge");
    const { data, error } = await api.POST(
      "/api/v1/environments/{environment_id}/git/repositories/{repository_id}/pulls/{number}/merge",
      { params: { path: { ...pathParams, number: pull.number } }, body: { method, delete_branch: deleteBranch } },
    );
    setBusy(null);
    if (error || !data) {
      setNotice({ error: true, text: errorMessage(error, "The pull request could not be merged.") });
      return;
    }
    setOpen(null);
    setNotice({ error: false, text: `#${pull.number} merged${data.branch_deleted ? ` and ${pull.head} deleted` : ""}.` });
    await load();
  }

  if (pulls && pulls.repository === null) {
    return (
      <section className="pp-section">
        <h3>Pull requests</h3>
        <p className="faint small">This project&apos;s remote is not on GitHub. Set one in the Changes tab.</p>
      </section>
    );
  }
  const onDefault = currentBranch !== null && currentBranch === pulls?.default_branch;
  return (
    <section className="pp-section">
      <h3>Pull requests {pulls?.repository && <span className="faint small mono">{pulls.repository}</span>}</h3>
      <div className="pp-row">
        <select aria-label="Which pull requests" value={state} onChange={(event) => setState(event.target.value as "open" | "closed")}>
          <option value="open">Open</option>
          <option value="closed">Closed</option>
        </select>
        {!draft && (
          <button
            type="button"
            className="button"
            disabled={!currentBranch || onDefault}
            title={onDefault ? "Create a branch for the change first" : undefined}
            onClick={() => setDraft({ title: "", body: "", base: "", isDraft: false })}
          >
            New pull request
          </button>
        )}
      </div>
      {draft && (
        <form className="pp-pr-form" onSubmit={(event) => void create(event)}>
          <p className="small">
            From <span className="mono">{currentBranch}</span> into{" "}
            <input
              aria-label="Base branch"
              className="mono pp-base"
              placeholder={pulls?.default_branch ?? "main"}
              value={draft.base}
              onChange={(event) => setDraft({ ...draft, base: event.target.value })}
            />
          </p>
          <input
            aria-label="Pull request title"
            placeholder="Title"
            value={draft.title}
            maxLength={256}
            onChange={(event) => setDraft({ ...draft, title: event.target.value })}
          />
          <textarea
            aria-label="Pull request description"
            rows={6}
            placeholder="Description (Markdown)"
            value={draft.body}
            onChange={(event) => setDraft({ ...draft, body: event.target.value })}
          />
          <label className="checkbox-row small">
            <input type="checkbox" checked={draft.isDraft} onChange={(event) => setDraft({ ...draft, isDraft: event.target.checked })} />
            Draft
          </label>
          <div className="pp-row">
            <button type="button" className="button button-ghost" disabled={busy !== null} onClick={() => void propose()}>
              {busy === "propose" ? "Proposing…" : "Propose title and description"}
            </button>
            <button type="submit" className="button" disabled={busy !== null || !draft.title.trim()}>
              Open pull request
            </button>
            <button type="button" className="button button-ghost" onClick={() => setDraft(null)}>
              Cancel
            </button>
          </div>
        </form>
      )}
      {notice && <p className={`small${notice.error ? " form-error" : ""}`}>{notice.text}</p>}
      {pulls === null ? (
        <p className="faint small">Loading…</p>
      ) : pulls.pulls.length === 0 ? (
        <p className="faint small">No {state} pull requests.</p>
      ) : (
        <ul className="pp-pr-list">
          {pulls.pulls.map((pull) => (
            <li key={pull.number}>
              <button type="button" className="link-button" aria-expanded={open?.number === pull.number} onClick={() => void show(pull.number)}>
                #{pull.number} {pull.title}
              </button>{" "}
              {pull.draft && <Badge>draft</Badge>} {pull.merged && <Badge tone="ok">merged</Badge>}
              <span className="faint small">
                {" "}
                <span className="mono">{pull.head}</span> → <span className="mono">{pull.base}</span> · {pull.author}
              </span>
              {open?.number === pull.number && <PullView pull={open} busy={busy === "merge"} onMerge={merge} />}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function PullView({
  pull,
  busy,
  onMerge,
}: {
  pull: PullDetail;
  busy: boolean;
  onMerge: (pull: PullDetail, method: MergeMethod, deleteBranch: boolean) => Promise<void>;
}) {
  const [method, setMethod] = useState<MergeMethod>("squash");
  const [deleteBranch, setDeleteBranch] = useState(true);
  const [file, setFile] = useState<string | null>(null);
  const shown = pull.files.find((item) => item.path === file);
  return (
    <div className="pp-pr-detail">
      {pull.body ? <pre className="pp-pr-body">{pull.body}</pre> : <p className="faint small">No description.</p>}
      {pull.checks.length > 0 && (
        <p className="small">
          Checks:{" "}
          {pull.checks.map((check) => (
            <Badge
              key={check.name}
              tone={check.conclusion === "success" ? "ok" : check.conclusion === "failure" ? "danger" : "neutral"}
            >
              {check.name} · {check.conclusion ?? check.status}
            </Badge>
          ))}
        </p>
      )}
      <ul className="pp-pr-files">
        {pull.files.map((item) => (
          <li key={item.path}>
            <button type="button" className="link-button mono" onClick={() => setFile(file === item.path ? null : item.path)}>
              {item.path}
            </button>{" "}
            <span className="faint small">
              {item.status} +{item.additions} −{item.deletions}
            </span>
          </li>
        ))}
      </ul>
      {shown && <DiffView diff={shown.patch} />}
      {pull.state === "open" && !pull.merged && (
        <div className="pp-row">
          <select aria-label="Merge method" value={method} onChange={(event) => setMethod(event.target.value as MergeMethod)}>
            <option value="squash">Squash and merge</option>
            <option value="merge">Merge commit</option>
            <option value="rebase">Rebase and merge</option>
          </select>
          <label className="checkbox-row small">
            <input type="checkbox" checked={deleteBranch} onChange={(event) => setDeleteBranch(event.target.checked)} />
            Delete {pull.head} after merging
          </label>
          <button
            type="button"
            className="button"
            disabled={busy || pull.draft || pull.mergeable === false}
            title={pull.draft ? "A draft cannot be merged" : pull.mergeable === false ? "GitHub reports conflicts" : undefined}
            onClick={() => void onMerge(pull, method, deleteBranch)}
          >
            Merge
          </button>
        </div>
      )}
      <a className="small" href={pull.html_url} target="_blank" rel="noreferrer">
        Open on GitHub
      </a>
    </div>
  );
}
