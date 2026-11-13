import { useBoardParallel } from "./useBoardParallel";

/** How many approved plans of the project build at the same time (project level only). */
export default function BoardParallelRow({ projectId }: { projectId: number }) {
  const parallel = useBoardParallel(projectId);
  const setting = parallel.setting;
  if (setting === null) return parallel.error ? <p className="form-error small">{parallel.error}</p> : null;
  return (
    <div className="pp-language">
      <label htmlFor="project-board-parallel">{setting.label}</label>
      <p className="faint small">{setting.description}</p>
      <select
        id="project-board-parallel"
        value={String(setting.value)}
        disabled={parallel.busy}
        onChange={(event) => void parallel.set(event.target.value)}
      >
        {setting.options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {parallel.error && <p className="form-error small">{parallel.error}</p>}
    </div>
  );
}
