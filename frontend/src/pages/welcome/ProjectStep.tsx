import { useCallback, useEffect, useRef, useState } from "react";

import { type Environment, type Project, api } from "../../api/client";
import { EmptyState, FieldRow, Panel, Segmented } from "../../components/workbench/Workbench";
import { useEnvironment } from "../../environment/EnvironmentProvider";
import NewProjectForm from "../chat/NewProjectForm";
import { useNewProject } from "../chat/useNewProject";
import { useCredentials } from "../settings/ConnectionsSection";
import CredentialField from "../settings/CredentialField";
import EnvironmentNameField from "../settings/EnvironmentNameField";

/** The projects of one environment, reloaded on demand. */
function useProjects(environmentId: number): [Project[] | null, () => Promise<void>] {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const reload = useCallback(async () => {
    const { data } = await api.GET("/api/v1/environments/{environment_id}/projects", {
      params: { path: { environment_id: environmentId } },
    });
    setProjects(data ?? []);
  }, [environmentId]);
  useEffect(() => {
    void reload();
  }, [reload]);
  return [projects, reload];
}

/** The first project in the environment the user starts in: empty, or cloned from a git link. */
function FirstProject({ environment, onChanged }: { environment: Environment; onChanged: () => void }) {
  const [projects, reload] = useProjects(environment.id);
  const [created, setCreated] = useState<string | null>(null);
  const form = useNewProject(environment.id, (project) => {
    setCreated(project.name);
    void reload();
    onChanged();
  });
  const [credentials, reloadCredentials] = useCredentials(environment.id);
  const github = credentials.find((credential) => credential.kind === "github_token");
  const opened = useRef(false);
  const { toggle } = form;

  useEffect(() => {
    // The form is the point of this step, so it starts open.
    if (opened.current) return;
    opened.current = true;
    toggle();
  }, [toggle]);

  return (
    <>
      <Panel title={`First project in ${environment.name}`}>
        <p className="muted small">
          A project is a folder YAAP works in, with its own git history. Start empty, or paste a git link to clone a
          repository.
        </p>
        {created && <p className="form-ok small">{created} is ready.</p>}
        {projects && projects.length > 0 && (
          <p className="small">
            Projects here: {projects.map((project) => project.name).join(", ")}
          </p>
        )}
        <div className="welcome-project-form">
          {form.name === null ? (
            <button type="button" className="button" onClick={toggle}>
              Another project
            </button>
          ) : (
            <NewProjectForm form={form} />
          )}
        </div>
      </Panel>
      <Panel title="GitHub token" flush aside="Optional: for private repositories and more GitHub requests per hour">
        {github ? (
          <CredentialField environmentId={environment.id} credential={github} onChanged={reloadCredentials} />
        ) : (
          <EmptyState>The token is set under Settings › Git and GitHub.</EmptyState>
        )}
      </Panel>
    </>
  );
}

/** Keep or rename Private and Work, choose the one to start in, and create a first project there. */
export default function ProjectStep({ onChanged }: { onChanged: () => void }) {
  const { environments, active, selectEnvironment } = useEnvironment();
  if (!active) return null;
  return (
    <>
      <Panel title="Environments" flush>
        <p className="muted small welcome-panel-text">
          Keys, projects and settings are kept per environment, so work and private things stay apart. Keep the names,
          or rename them.
        </p>
        {environments.map((environment) => (
          <EnvironmentNameField
            key={environment.id}
            environment={environment}
            label={environment.name}
            hint="Name and colour"
          />
        ))}
        <FieldRow label="Start in" hint="The environment the app opens in">
          <Segmented
            label="Environment to start in"
            value={String(active.id)}
            options={environments.map((environment) => ({ value: String(environment.id), label: environment.name }))}
            onChange={(value) => selectEnvironment(Number(value))}
          />
        </FieldRow>
      </Panel>
      <FirstProject key={active.id} environment={active} onChanged={onChanged} />
    </>
  );
}
