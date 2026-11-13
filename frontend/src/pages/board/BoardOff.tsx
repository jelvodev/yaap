import type { Project } from "../../api/client";
import type { Board } from "./boardApi";
import { useBoardSwitch } from "./useBoardSwitch";

const SOURCES: Record<Board["enabled_source"], string> = {
  default: "YAAP's default",
  global: "the global setting",
  environment: "the environment's setting",
  project: "this project's setting",
};

/** A project whose board is switched off, with the way to switch it on. */
export default function BoardOff({
  projectId,
  source,
  onProjectChanged,
  reload,
}: {
  projectId: number;
  source: Board["enabled_source"];
  onProjectChanged: (project: Project) => void;
  reload: () => Promise<void>;
}) {
  const switcher = useBoardSwitch(projectId, onProjectChanged);

  async function switchOn() {
    await switcher.set(true);
    await reload();
  }

  return (
    <div className="board-empty">
      <h3>The board is switched off for this project</h3>
      <p className="muted">It is off by {SOURCES[source]}.</p>
      {switcher.error && <p className="form-error small">{switcher.error}</p>}
      <button type="button" className="button button-primary" disabled={switcher.busy} onClick={() => void switchOn()}>
        Switch on for this project
      </button>
    </div>
  );
}
