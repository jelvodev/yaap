import type { Project } from "../../api/client";
import { PlusIcon, SettingsIcon } from "../../components/icons";
import NewProjectForm from "./NewProjectForm";
import { useNewProject } from "./useNewProject";

interface ProjectPickerProps {
  environmentId: number;
  projects: Project[];
  scope: number | null;
  onChoose: (projectId: number | null) => void;
  onOpenPanel: (project: Project) => void;
  onCreated: (project: Project) => void;
}

/** The projects of the environment in the sidebar, "No project", and the form for a new one. */
export default function ProjectPicker(props: ProjectPickerProps) {
  const { projects, scope, onChoose, onOpenPanel } = props;
  const form = useNewProject(props.environmentId, props.onCreated);
  return (
    <div className="project-picker">
      <div className="sidebar-group-label sidebar-group-heading">
        <span>Projects</span>
        <button type="button" className="icon-button" title="New project" aria-label="New project" onClick={form.toggle}>
          <PlusIcon />
        </button>
      </div>
      <NewProjectForm form={form} />
      <ul className="conversation-list">
        <li>
          <button
            type="button"
            className={scope === null ? "scope-item active" : "scope-item"}
            onClick={() => onChoose(null)}
          >
            No project
          </button>
        </li>
        {projects.map((project) => (
          <li key={project.id} className="scope-row">
            <button
              type="button"
              className={scope === project.id ? "scope-item active" : "scope-item"}
              title={project.folder}
              onClick={() => onChoose(project.id)}
            >
              {project.name}
            </button>
            <button
              type="button"
              className="scope-settings"
              aria-label={`${project.name}: project panel`}
              title="Changes, memory and settings"
              onClick={() => onOpenPanel(project)}
            >
              <SettingsIcon size={13} />
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
