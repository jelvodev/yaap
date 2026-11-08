import { useCallback, useEffect, useState } from "react";

import { type Preset, type Project, api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";
import { useEnvironment } from "../../environment/EnvironmentProvider";
import DefaultSkillsSection from "../DefaultSkillsSection";
import ProjectContext from "./ProjectContext";
import ProjectGitRemote from "./ProjectGitRemote";
import ProjectLanguage from "./ProjectLanguage";
import ProjectSandbox from "./ProjectSandbox";
import ProjectTarget from "./ProjectTarget";
import type { ProjectRepository } from "./useProjectRepository";
import ModelPolicyPanel from "../ModelPolicyPanel";
import PermissionsSection from "../PermissionsSection";
import ProcessesSection from "../ProcessesSection";
import VaultList from "../VaultList";
import McpServersPanel from "../McpServersPanel";
import { useConfirm } from "../dialog/ConfirmDialog";

type Agent = components["schemas"]["AgentOut"];

/** A project's name, what new conversations start with, its processes and permissions, moving and removing it. */
export default function ProjectSettings({
  project,
  onChanged,
  onRemoved,
  git,
}: {
  project: Project;
  onChanged: (project: Project) => void;
  onRemoved: () => void;
  /** The project's git state, shared with the Changes panel, for the remote. */
  git: ProjectRepository;
}) {
  const confirm = useConfirm();
  const { active, environments, selectEnvironment } = useEnvironment();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [name, setName] = useState(project.name);
  const [notice, setNotice] = useState<{ error: boolean; text: string } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [moveTarget, setMoveTarget] = useState<number | null>(null);
  const [moving, setMoving] = useState(false);

  const load = useCallback(async () => {
    const [agentsResult, presetsResult] = await Promise.all([
      api.GET("/api/v1/environments/{environment_id}/agents", {
        params: { path: { environment_id: project.environment_id }, query: { project_id: project.id } },
      }),
      api.GET("/api/v1/environments/{environment_id}/presets", { params: { path: { environment_id: project.environment_id } } }),
    ]);
    setAgents(agentsResult.data ?? []);
    setPresets(presetsResult.data ?? []);
  }, [project.id, project.environment_id]);

  useEffect(() => {
    void load();
    setName(project.name);
  }, [load, project.name]);

  async function update(body: { name?: string; default_agent?: string | null; default_preset?: string | null }) {
    const { data, error } = await api.PATCH("/api/v1/projects/{project_id}", { params: { path: { project_id: project.id } }, body });
    if (error || !data) {
      setNotice({ error: true, text: errorMessage(error) });
      return;
    }
    onChanged(data);
    setNotice({ error: false, text: "Saved." });
  }

  async function remove() {
    const { error } = await api.DELETE("/api/v1/projects/{project_id}", { params: { path: { project_id: project.id } } });
    if (error) {
      setConfirmDelete(false);
      setNotice({ error: true, text: errorMessage(error) });
      return;
    }
    onRemoved();
  }

  async function move() {
    if (moveTarget === null) return;
    setMoving(true);
    const { data, error } = await api.POST("/api/v1/projects/{project_id}/move", {
      params: { path: { project_id: project.id } },
      body: { environment_id: moveTarget },
    });
    setMoving(false);
    if (error || !data) {
      setNotice({ error: true, text: errorMessage(error, "The project could not be moved.") });
      return;
    }
    const target = environments.find((environment) => environment.id === moveTarget);
    setMoveTarget(null);
    const cleared = data.cleared.length ? ` Cleared because ${target?.name ?? "it"} does not have it: ${data.cleared.join(", ")}.` : "";
    await confirm({ title: "Project moved", message: `Moved to ${target?.name ?? "the other environment"}.${cleared}`, cancelLabel: null });
    selectEnvironment(data.project.environment_id);
  }

  return (
    <div className="pp-settings">
      <p className="muted small mono pp-folder">
        workspace/{project.folder}
        {project.repository_url ? ` · ${project.repository_url}` : ""}
      </p>
      {notice && <p className={notice.error ? "form-error small" : "muted small"}>{notice.text}</p>}

      <section className="pp-section">
        <h3>Name</h3>
        <form
          className="pp-row"
          onSubmit={(event) => {
            event.preventDefault();
            void update({ name: name.trim() });
          }}
        >
          <input aria-label="Project name" value={name} maxLength={120} onChange={(event) => setName(event.target.value)} />
          <button type="submit" className="button" disabled={!name.trim() || name.trim() === project.name}>
            Rename
          </button>
        </form>
      </section>

      <section className="pp-section">
        <h3>New conversations</h3>
        <p className="faint small">What a new conversation in this project starts with. You can still change it per conversation.</p>
        <label className="pp-label" htmlFor="project-agent">
          Agent
        </label>
        <select
          id="project-agent"
          value={project.default_agent ?? ""}
          onChange={(event) => void update({ default_agent: event.target.value || null })}
        >
          <option value="">YAAP (no agent)</option>
          {agents.map((agent) => (
            <option key={agent.name} value={agent.name}>
              {agent.name}
            </option>
          ))}
        </select>
        <label className="pp-label" htmlFor="project-preset">
          Preset
        </label>
        <select
          id="project-preset"
          value={project.default_preset ?? ""}
          onChange={(event) => void update({ default_preset: event.target.value || null })}
        >
          <option value="">The environment's default</option>
          {presets.map((preset) => (
            <option key={preset.id} value={preset.name}>
              {preset.name}
            </option>
          ))}
        </select>
      </section>

      <ProcessesSection projectId={project.id} />

      <section className="pp-section">
        <h3>Vault</h3>
        <p className="faint small">
          The project's own keys and the secrets the agent uses in commands as {"{{secret:NAME}}"} without seeing them.
          A key here replaces the environment's default of that kind for this project; a secret here overrides one of
          the same name.
        </p>
        <VaultList environmentId={project.environment_id} projectId={project.id} />
      </section>

      <section className="pp-section">
        <h3>MCP servers</h3>
        <McpServersPanel environmentId={project.environment_id} projectId={project.id} />
      </section>

      <section className="pp-section">
        <h3>Where the agent works</h3>
        <ProjectTarget projectId={project.id} environmentId={project.environment_id} />
        <ProjectSandbox projectId={project.id} />
      </section>

      <section className="pp-section">
        <h3>Git</h3>
        <ProjectGitRemote environmentId={project.environment_id} git={git} />
      </section>

      <section className="pp-section">
        <h3>Language</h3>
        <ProjectLanguage projectId={project.id} />
      </section>

      <section className="pp-section">
        <h3>Context</h3>
        <ProjectContext projectId={project.id} />
      </section>

      <section className="pp-section">
        <h3>Permissions</h3>
        <p className="faint small">What the agent may do without asking in this project.</p>
        <PermissionsSection level={{ kind: "project", id: project.id, name: project.name }} />
        <DefaultSkillsSection level={{ kind: "project", id: project.id, name: project.name }} />
      </section>

      <section className="pp-section">
        <h3>Model policy</h3>
        <p className="faint small">Narrow the environment's model policy for this project.</p>
        <ModelPolicyPanel
          key={project.id}
          level={{ kind: "project", environmentId: project.environment_id, projectId: project.id, name: project.name }}
        />
      </section>

      {environments.length > 1 && (
        <section className="pp-section">
          <h3>Move</h3>
          <p className="faint small">
            Moves the project, its folder and its conversations to another environment. From then on it works with that
            environment's API keys and GitHub token, its Blueprint and its presets. Usage already recorded stays with{" "}
            {active?.name ?? "this environment"}. Earlier replies made through Claude Code are passed along as text.
          </p>
          <div className="pp-row">
            <select
              aria-label="Target environment"
              value={moveTarget ?? ""}
              onChange={(event) => setMoveTarget(event.target.value ? Number(event.target.value) : null)}
            >
              <option value="">Choose an environment…</option>
              {environments
                .filter((environment) => environment.id !== project.environment_id)
                .map((environment) => (
                  <option key={environment.id} value={environment.id}>
                    {environment.name}
                  </option>
                ))}
            </select>
            <button type="button" className="button" disabled={moveTarget === null || moving} onClick={() => void move()}>
              {moving ? "Moving…" : "Move"}
            </button>
          </div>
        </section>
      )}

      <section className="pp-section">
        <h3>Remove</h3>
        <p className="faint small">Removes the project from YAAP. Its folder stays on disk. A project with conversations cannot be removed.</p>
        {confirmDelete ? (
          <div className="pp-row">
            <button type="button" className="button button-danger" onClick={() => void remove()}>
              Remove {project.name}
            </button>
            <button type="button" className="button button-ghost" onClick={() => setConfirmDelete(false)}>
              Cancel
            </button>
          </div>
        ) : (
          <button type="button" className="button" onClick={() => setConfirmDelete(true)}>
            Remove project…
          </button>
        )}
      </section>
    </div>
  );
}
