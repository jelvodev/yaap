import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { type GitRepository, api, errorMessage } from "../../api/client";
import { LayersIcon } from "../../components/icons";
import { Badge, EmptyState, Panel } from "../../components/workbench/Workbench";
import { useEnvironment } from "../../environment/EnvironmentProvider";
import CredentialField from "./CredentialField";
import { GitHubRepositoriesPanel, TokenCheckPanel } from "./GitHubPanels";
import { useCredentials } from "./ConnectionsSection";
import { type LevelSettings, type SettingsLevel, SectionHeader, TAB_OVERRIDE_KEYS, TextSetting } from "./shared";

const AUTHOR_KEYS = ["git_author_name", "git_author_email"];

type RemoteAction = "fetch" | "push" | "pull";

function LocalState({ repository }: { repository: GitRepository }) {
  if (repository.error) {
    return (
      <Badge tone="danger" title={repository.error}>
        not a git repository
      </Badge>
    );
  }
  if (repository.changes.length === 0) return <Badge tone="ok">clean</Badge>;
  return <Badge tone="warn">{repository.changes.length} changed</Badge>;
}

function RemoteState({ repository }: { repository: GitRepository }) {
  if (repository.error) return null;
  if (!repository.remote_url) return <Badge>no remote</Badge>;
  if (!repository.upstream) return <Badge tone="warn">not pushed yet</Badge>;
  if (!repository.ahead && !repository.behind) return <Badge tone="ok">in sync</Badge>;
  return (
    <span className="settings-badges">
      {repository.ahead > 0 && <Badge tone="warn">↑{repository.ahead} not pushed</Badge>}
      {repository.behind > 0 && <Badge tone="warn">↓{repository.behind} behind</Badge>}
    </span>
  );
}

function blueprintLink(repository: GitRepository, environmentId: number): string {
  return repository.id === "blueprint-global"
    ? "/blueprint?level=global"
    : `/blueprint?level=environment&environment=${environmentId}`;
}

/** Every repository of an environment: the Blueprint layers and the projects, with their local and remote state. */
function RepositoriesPanel({
  environmentId,
  environmentName,
  repositories,
  busy,
  onAction,
}: {
  environmentId: number;
  environmentName: string;
  repositories: GitRepository[] | null;
  busy: string | null;
  onAction: (repository: GitRepository, action: RemoteAction) => void;
}) {
  return (
    <Panel title={`Repositories in ${environmentName}`} flush aside="Diff and commit happen in the chat's project panel and the Blueprint">
      {repositories === null ? (
        <EmptyState>Loading…</EmptyState>
      ) : repositories.length === 0 ? (
        <EmptyState>No repositories.</EmptyState>
      ) : (
        <div className="wb-table-scroll settings-scroll">
          <table className="wb-table settings-repositories">
            <thead>
              <tr>
                <th scope="col">Repository</th>
                <th scope="col">Kind</th>
                <th scope="col">Branch</th>
                <th scope="col">Local</th>
                <th scope="col">Remote</th>
                <th scope="col">
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {repositories.map((repository) => {
                const isBlueprint = repository.kind === "blueprint";
                const canPush =
                  !repository.error &&
                  Boolean(repository.remote_url) &&
                  repository.has_commits &&
                  (repository.ahead > 0 || !repository.upstream);
                const rowBusy = busy?.startsWith(`${repository.id}:`) ? busy.split(":")[1] : null;
                return (
                  <tr key={repository.id}>
                    <td>
                      <span className="settings-repository-name" title={repository.folder}>
                        {isBlueprint && <LayersIcon size={13} />}
                        {repository.label}
                      </span>
                      {repository.remote_url && <div className="settings-repository-remote mono">{repository.remote_url}</div>}
                    </td>
                    <td className="settings-muted-cell">{isBlueprint ? "Blueprint" : "Project"}</td>
                    <td className="mono">{repository.error ? "–" : (repository.branch ?? "detached")}</td>
                    <td>
                      <LocalState repository={repository} />
                    </td>
                    <td>
                      <RemoteState repository={repository} />
                    </td>
                    <td className="wb-num">
                      <span className="settings-row-actions">
                        {repository.behind > 0 && (
                          <button
                            type="button"
                            className="button"
                            disabled={Boolean(busy)}
                            title="Fast-forward only; diverged history is never merged without you"
                            onClick={() => onAction(repository, "pull")}
                          >
                            {rowBusy === "pull" ? "Pulling…" : "Pull"}
                          </button>
                        )}
                        {canPush && (
                          <button
                            type="button"
                            className={isBlueprint ? "button button-primary" : "button"}
                            disabled={Boolean(busy)}
                            onClick={() => onAction(repository, "push")}
                          >
                            {rowBusy === "push" ? "Pushing…" : "Push"}
                          </button>
                        )}
                        {isBlueprint ? (
                          <Link className="button button-ghost" to={blueprintLink(repository, environmentId)}>
                            Open in Blueprint
                          </Link>
                        ) : (
                          <Link
                            className="button button-ghost"
                            to={`/chat?project=${repository.id.replace("project-", "")}&panel=changes`}
                          >
                            Open in chat
                          </Link>
                        )}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

/** GitHub token and commit author, and, in an environment, the state of every repository. */
export default function GitSection({ level, store }: { level: SettingsLevel; store: LevelSettings }) {
  const navigate = useNavigate();
  const { environments } = useEnvironment();
  const environmentId = level.kind === "environment" ? level.environment.id : null;
  const [credentials, reloadCredentials] = useCredentials(environmentId);
  const [repositories, setRepositories] = useState<GitRepository[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ error: boolean; text: string } | null>(null);

  const reload = useCallback(async () => {
    if (environmentId === null) return;
    const { data } = await api.GET("/api/v1/environments/{environment_id}/git/repositories", {
      params: { path: { environment_id: environmentId } },
    });
    setRepositories(data ?? []);
  }, [environmentId]);

  useEffect(() => {
    setRepositories(null);
    setNotice(null);
    void reload();
  }, [reload]);

  const replace = (updated: GitRepository) =>
    setRepositories((list) => list?.map((item) => (item.id === updated.id ? updated : item)) ?? null);

  async function run(repository: GitRepository, action: RemoteAction): Promise<string | null> {
    if (environmentId === null) return `${repository.label}: no environment is selected.`;
    const { data, error } = await api.POST(
      `/api/v1/environments/{environment_id}/git/repositories/{repository_id}/${action}`,
      { params: { path: { environment_id: environmentId, repository_id: repository.id } } },
    );
    if (error || !data) return `${repository.label}: ${errorMessage(error)}`;
    replace(data);
    return null;
  }

  async function act(repository: GitRepository, action: RemoteAction) {
    setBusy(`${repository.id}:${action}`);
    setNotice(null);
    const failure = await run(repository, action);
    setBusy(null);
    setNotice(
      failure
        ? { error: true, text: failure }
        : { error: false, text: `${repository.label}: ${action === "push" ? "pushed" : "pulled"}.` },
    );
  }

  async function fetchAll() {
    if (!repositories) return;
    setBusy("all:fetch");
    setNotice(null);
    const targets = repositories.filter((repository) => !repository.error && repository.remote_url);
    const failures = (await Promise.all(targets.map((repository) => run(repository, "fetch")))).filter(
      (failure): failure is string => failure !== null,
    );
    setBusy(null);
    setNotice(
      failures.length
        ? { error: true, text: failures.join(" · ") }
        : { error: false, text: `Fetched ${targets.length} repositor${targets.length === 1 ? "y" : "ies"}.` },
    );
  }

  const github = credentials.filter((credential) => credential.kind === "github_token");
  const authors = store.settings.filter((setting) => AUTHOR_KEYS.includes(setting.key));

  return (
    <>
      <SectionHeader
        level={level}
        store={store}
        title="Git and GitHub"
        overrideKeys={TAB_OVERRIDE_KEYS.git}
        actions={
          level.kind === "environment" ? (
            <button
              type="button"
              className="button"
              disabled={Boolean(busy) || !repositories?.some((repository) => repository.remote_url)}
              onClick={() => void fetchAll()}
            >
              {busy === "all:fetch" ? "Fetching…" : "Fetch all"}
            </button>
          ) : undefined
        }
        info={
          level.kind === "global" ? (
            <p>
              The name and email on the commits you make in YAAP. An environment can use another address. The GitHub
              token and the repositories are per environment.
            </p>
          ) : (
            <>
              <p>
                The Blueprint layers and the projects of {level.environment.name}. Remote actions use this
                environment's GitHub token; pull only fast-forwards, so diverged history is never merged without you.
              </p>
              <p>The token is used only for this environment's projects and Blueprint.</p>
            </>
          )
        }
      />
      <div className="wb-body wb-stack settings-git">
        {notice && <p className={notice.error ? "form-error" : "form-ok"}>{notice.text}</p>}
        {level.kind === "environment" ? (
          <RepositoriesPanel
            environmentId={level.environment.id}
            environmentName={level.environment.name}
            repositories={repositories}
            busy={busy}
            onAction={(repository, action) => void act(repository, action)}
          />
        ) : (
          <Panel title="Repositories" flush>
            <EmptyState>Repositories and the GitHub token are per environment.</EmptyState>
            <div className="settings-link-row">
              {environments.map((environment) => (
                <button
                  key={environment.id}
                  type="button"
                  className="button button-ghost"
                  onClick={() => navigate(`/settings/git?environment=${environment.id}`)}
                >
                  <span className="dot" style={{ background: environment.colour }} />
                  {environment.name}
                </button>
              ))}
            </div>
          </Panel>
        )}

        {level.kind === "environment" && <GitHubRepositoriesPanel key={level.environment.id} environmentId={level.environment.id} />}

        <Panel title="Account" flush>
          {level.kind === "environment" &&
            github.map((credential) => (
              <CredentialField
                key={`${level.environment.id}-${credential.kind}`}
                environmentId={level.environment.id}
                credential={credential}
                onChanged={reloadCredentials}
              />
            ))}
          {level.kind === "environment" && <TokenCheckPanel environmentId={level.environment.id} refresh={github} />}
          {authors.map((setting) => (
            <TextSetting
              key={setting.key}
              level={level}
              setting={setting}
              placeholder={setting.key === "git_author_email" ? "you@example.com" : "Empty: commits are made as YAAP"}
              onSave={(value) => store.save(setting.key, value)}
              onReset={() => store.reset(setting.key)}
            />
          ))}
        </Panel>
      </div>
    </>
  );
}
