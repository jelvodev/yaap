import { type BlueprintItem } from "../../api/client";
import { type Level } from "../../components/workbench/LevelPicker";
import ImportItemRow from "./ImportItemRow";
import { SOURCES } from "./importLogic";
import { type ImportSource, useSetupImport } from "./useSetupImport";
import "./import-panel.css";

/** Import a setup from another tool, or Markdown files: the utility model helps, the user reviews, then it is written. */
export default function ImportPanel({
  level,
  levelName,
  utilityEnvironmentId,
  onClose,
  onImported,
  source = "upload",
}: {
  level: Level;
  levelName: string;
  utilityEnvironmentId: number | null;
  /** Kept for callers; whether an item replaces another comes from the analysis itself. */
  items?: BlueprintItem[];
  onClose?: () => void;
  onImported: (written: string[]) => void;
  /** An uploaded file, or the project's own folder (only at a project's level). */
  source?: ImportSource;
}) {
  const imp = useSetupImport({ level, utilityEnvironmentId, onImported });
  const chosenSource = SOURCES.find((entry) => entry.layout === imp.layout);
  const count = imp.chosen.length;

  return (
    <section className="card import-panel">
      <div className="card-header">
        <h3>Import into {levelName}</h3>
        {onClose && (
          <button type="button" className="button button-ghost" onClick={onClose}>
            Close
          </button>
        )}
      </div>
      {source === "project" ? (
        <>
          <p className="muted small">
            Reads the setup files in this project's own folder. You review every item before anything is written. Each
            item becomes its own commit.
          </p>
          <button type="button" className="button" disabled={imp.busy !== null} onClick={() => void imp.analyzeProject()}>
            {imp.busy === "analyzing" ? "Reading…" : "Read the project's setup"}
          </button>
        </>
      ) : (
        <>
          <p className="muted small">
            YAAP runs in a container, so it cannot read your home folder itself: zip the folder and upload the zip.
            Settings, hooks, MCP and sign-in files are listed but never read. You review every item before anything is
            written. Each item becomes its own commit.
          </p>
          <div className="import-sources">
            <label className="pp-label" htmlFor="import-from">
              Where it comes from
            </label>
            <select
              id="import-from"
              value={imp.layout}
              disabled={imp.busy !== null}
              onChange={(event) => {
                const next = SOURCES.find((entry) => entry.layout === event.target.value);
                if (next) imp.setLayout(next.layout);
              }}
            >
              {SOURCES.map((entry) => (
                <option key={entry.layout} value={entry.layout}>
                  {entry.label}
                </option>
              ))}
            </select>
            {chosenSource && <p className="faint small">{chosenSource.hint}</p>}
          </div>
          <label className="button">
            {imp.busy === "analyzing" ? "Reading…" : "Choose .md or .zip…"}
            <input
              type="file"
              accept=".md,.markdown,.txt,.zip"
              className="visually-hidden"
              disabled={imp.busy !== null}
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                if (file) void imp.analyzeUpload(file);
              }}
            />
          </label>
        </>
      )}
      {imp.error && <p className="form-error">{imp.error}</p>}

      {imp.rows && (
        <>
          {imp.rows.length === 0 && <p className="muted small">Nothing to import was found.</p>}
          <ul className="import-list">
            {imp.rows.map((row, index) => (
              <ImportItemRow
                key={`${row.source}-${index}`}
                row={row}
                levelName={levelName}
                levelIsGlobal={imp.levelIsGlobal}
                onChange={(patch) => imp.change(index, patch)}
                onToggleFile={(fileIndex, on) => imp.toggleFile(index, fileIndex, on)}
              />
            ))}
          </ul>
          {(imp.notImported.length > 0 || imp.skipped > 0) && (
            <div className="import-not">
              <h4>Not imported</h4>
              {imp.notImported.length > 0 && (
                <ul className="small">
                  {imp.notImported.map((entry) => (
                    <li key={entry.path}>
                      <span className="mono">{entry.path}</span> <span className="faint">{entry.reason}</span>
                    </li>
                  ))}
                </ul>
              )}
              {imp.skipped > 0 && (
                <p className="faint small">
                  {imp.skipped} other file{imp.skipped === 1 ? "" : "s"} skipped.
                </p>
              )}
            </div>
          )}
          {imp.duplicate && <p className="form-error">Two items would be written to {imp.duplicate}; rename or skip one.</p>}
          {imp.invalid && (
            <p className="form-error">{imp.invalid.name || "An empty name"}: use lowercase letters, digits and hyphens.</p>
          )}
          <div className="form-row">
            <button
              type="button"
              className="button button-primary"
              disabled={imp.busy !== null || count === 0 || Boolean(imp.duplicate) || Boolean(imp.invalid)}
              onClick={() => void imp.apply()}
            >
              {imp.busy === "writing" ? "Writing…" : `Import ${count} item${count === 1 ? "" : "s"}`}
            </button>
          </div>
        </>
      )}
    </section>
  );
}
