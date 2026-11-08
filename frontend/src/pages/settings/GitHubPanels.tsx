import { type FormEvent, useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";
import {
  Badge,
  EmptyState,
  FieldRow,
  Panel,
} from "../../components/workbench/Workbench";

type TokenCheck = components["schemas"]["TokenCheckOut"];
type GitHubRepository = components["schemas"]["GitHubRepositoryOut"];

/** What the environment's GitHub token may do (choice 15). */
export function TokenCheckPanel({
  environmentId,
  refresh,
}: {
  environmentId: number;
  refresh: unknown;
}) {
  const [check, setCheck] = useState<TokenCheck | null>(null);
  useEffect(() => {
    void api
      .GET("/api/v1/environments/{environment_id}/github/token", {
        params: { path: { environment_id: environmentId } },
      })
      .then(({ data }) => setCheck(data ?? null));
  }, [environmentId, refresh]);
  if (!check || check.kind === "none") return null;
  const tone =
    check.kind === "classic"
      ? check.missing.length
        ? "danger"
        : "ok"
      : "neutral";
  return (
    <FieldRow
      label="Token rights"
      hint={check.login ? `Signed in as ${check.login}` : undefined}
    >
      <Badge tone={tone}>
        {check.kind === "classic" ? "classic token" : "fine-grained token"}
      </Badge>{" "}
      <span className="small">{check.message}</span>
    </FieldRow>
  );
}

/** The GitHub repositories the token reaches, templates, and a new repository. */
export function GitHubRepositoriesPanel({
  environmentId,
}: {
  environmentId: number;
}) {
  const navigate = useNavigate();
  const [repositories, setRepositories] = useState<GitHubRepository[] | null>(
    null,
  );
  const [problem, setProblem] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ error: boolean; text: string } | null>(
    null,
  );
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState({
    name: "",
    description: "",
    private: true,
    template: "",
    project: true,
  });

  const load = useCallback(async () => {
    const { data, error } = await api.GET(
      "/api/v1/environments/{environment_id}/github/repositories",
      {
        params: { path: { environment_id: environmentId } },
      },
    );
    if (data) {
      setRepositories(data);
      setProblem(null);
    } else {
      setRepositories([]);
      setProblem(errorMessage(error, "GitHub could not be read."));
    }
  }, [environmentId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function toggleTemplate(repository: GitHubRepository) {
    const [owner, name] = repository.full_name.split("/");
    const { data, error } = await api.PATCH(
      "/api/v1/environments/{environment_id}/github/repositories/{owner}/{name}",
      {
        params: { path: { environment_id: environmentId, owner, name } },
        body: { is_template: !repository.is_template },
      },
    );
    if (error || !data) {
      setNotice({ error: true, text: errorMessage(error) });
      return;
    }
    setRepositories((list) =>
      (list ?? []).map((item) =>
        item.full_name === data.full_name
          ? { ...data, projects: item.projects }
          : item,
      ),
    );
    setNotice({
      error: false,
      text: `${data.full_name} is ${data.is_template ? "now a template" : "no longer a template"}.`,
    });
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    const name = draft.name.trim();
    const what = draft.template
      ? `from the template ${draft.template}`
      : "empty, with a README";
    if (
      !window.confirm(
        `Create the ${draft.private ? "private" : "public"} repository ${name} on GitHub, ${what}?`,
      )
    )
      return;
    setBusy(true);
    const { data, error } = await api.POST(
      "/api/v1/environments/{environment_id}/github/repositories",
      {
        params: { path: { environment_id: environmentId } },
        body: {
          name,
          private: draft.private,
          description: draft.description.trim(),
          template: draft.template || null,
          project_name: draft.project ? name : null,
        },
      },
    );
    setBusy(false);
    if (error || !data) {
      setNotice({
        error: true,
        text: errorMessage(error, "The repository could not be created."),
      });
      await load();
      return;
    }
    setDraft({
      name: "",
      description: "",
      private: true,
      template: "",
      project: true,
    });
    setNotice({
      error: false,
      text: `${data.repository.full_name} created${data.project_id ? " and cloned into a new project" : ""}.`,
    });
    await load();
  }

  const templates = (repositories ?? []).filter(
    (repository) => repository.is_template,
  );
  const shown = (repositories ?? []).filter((repository) =>
    repository.full_name.toLowerCase().includes(filter.trim().toLowerCase()),
  );
  return (
    <Panel
      title="GitHub repositories"
      flush
      aside="What the environment's token can reach; templates start new projects"
    >
      {problem ? (
        <EmptyState>{problem}</EmptyState>
      ) : repositories === null ? (
        <EmptyState>Loading…</EmptyState>
      ) : (
        <>
          <div className="github-filter">
            <input
              aria-label="Filter repositories"
              placeholder="Filter"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            />
          </div>
          <div className="wb-table-scroll github-repositories">
            <table className="wb-table">
              <thead>
                <tr>
                  <th>Repository</th>
                  <th>Visibility</th>
                  <th>Default branch</th>
                  <th>Project</th>
                  <th>Template</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((repository) => (
                  <tr key={repository.full_name}>
                    <td>
                      <a
                        href={repository.html_url}
                        target="_blank"
                        rel="noreferrer"
                        className="mono small"
                      >
                        {repository.full_name}
                      </a>
                    </td>
                    <td>
                      <Badge tone={repository.private ? "neutral" : "warn"}>
                        {repository.private ? "private" : "public"}
                      </Badge>
                    </td>
                    <td className="mono small">{repository.default_branch}</td>
                    <td className="small">
                      {repository.projects.length ? (
                        repository.projects.join(", ")
                      ) : (
                        <span className="faint">—</span>
                      )}
                    </td>
                    <td>
                      <label className="checkbox-row small">
                        <input
                          type="checkbox"
                          checked={repository.is_template}
                          onChange={() => void toggleTemplate(repository)}
                          aria-label={`${repository.full_name} is a template`}
                        />
                        {repository.is_template ? "template" : ""}
                      </label>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {notice && (
        <p
          className={`small github-notice${notice.error ? " form-error" : ""}`}
        >
          {notice.text}
        </p>
      )}
      {!problem && repositories !== null && (
        <form className="target-add" onSubmit={(event) => void create(event)}>
          <h4>New repository</h4>
          <FieldRow label="Name">
            <input
              className="mono"
              value={draft.name}
              maxLength={100}
              pattern="[A-Za-z0-9._\-]+"
              placeholder="my-app"
              onChange={(event) =>
                setDraft({ ...draft, name: event.target.value })
              }
            />
          </FieldRow>
          <FieldRow label="Start from">
            <select
              value={draft.template}
              onChange={(event) =>
                setDraft({ ...draft, template: event.target.value })
              }
            >
              <option value="">Empty, with a README</option>
              {templates.map((template) => (
                <option key={template.full_name} value={template.full_name}>
                  The template {template.full_name}
                </option>
              ))}
            </select>
          </FieldRow>
          <FieldRow label="Description">
            <input
              value={draft.description}
              maxLength={350}
              onChange={(event) =>
                setDraft({ ...draft, description: event.target.value })
              }
            />
          </FieldRow>
          <FieldRow label="Options">
            <span className="github-options">
              <label className="checkbox-row small">
                <input
                  type="checkbox"
                  checked={draft.private}
                  onChange={(event) =>
                    setDraft({ ...draft, private: event.target.checked })
                  }
                />
                Private
              </label>
              <label className="checkbox-row small">
                <input
                  type="checkbox"
                  checked={draft.project}
                  onChange={(event) =>
                    setDraft({ ...draft, project: event.target.checked })
                  }
                />
                Clone it into a new project
              </label>
            </span>
          </FieldRow>
          <div className="settings-inline-form">
            <button
              type="submit"
              className="button"
              disabled={busy || !draft.name.trim() || Boolean(problem)}
            >
              {busy ? "Creating…" : "Create repository"}
            </button>
            {notice && !notice.error && draft.project && (
              <button
                type="button"
                className="button button-ghost"
                onClick={() => navigate("/chat")}
              >
                Go to the chat
              </button>
            )}
          </div>
        </form>
      )}
    </Panel>
  );
}
