import type { Project } from "../../api/client";
import { useBoardSwitch } from "./useBoardSwitch";

function sourceLabel(source: string): string {
  return source === "default" ? "YAAP's default" : source === "global" ? "Global" : "the environment";
}

/** The project's board switch in its settings: on, off, or inherited from above. */
export default function BoardSwitchRow({
  project,
  onChanged,
}: {
  project: Project;
  onChanged: (project: Project) => void;
}) {
  const board = useBoardSwitch(project.id, onChanged);
  const setting = board.setting;
  if (setting === null) return board.error ? <p className="form-error small">{board.error}</p> : null;
  const inherited = setting.inherited_value === true ? "on" : "off";
  const value = board.own ? (setting.level_value === true ? "on" : "off") : "inherit";
  return (
    <div className="pp-language">
      <label htmlFor="project-board-switch">{setting.label}</label>
      <p className="faint small">{setting.description}</p>
      <select
        id="project-board-switch"
        value={value}
        disabled={board.busy}
        onChange={(event) => {
          const choice = event.target.value;
          void board.set(choice === "inherit" ? null : choice === "on");
        }}
      >
        <option value="inherit">
          Inherited from {sourceLabel(setting.inherited_source)}: {inherited}
        </option>
        <option value="on">On for this project</option>
        <option value="off">Off for this project</option>
      </select>
      {board.error && <p className="form-error small">{board.error}</p>}
    </div>
  );
}
