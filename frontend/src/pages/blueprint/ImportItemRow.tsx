import { useState } from "react";

import MarkedText, { ReviewWarnings } from "../../components/MarkedText";
import { replacesExisting } from "./importLogic";
import { type ItemKind, KINDS, pathFor } from "./model";
import type { ImportRow } from "./useSetupImport";

const TOOL_LABEL = { claude_code: "Claude Code", codex: "Codex", cursor: "Cursor", copilot: "GitHub Copilot" } as const;

/** One proposed item: what it is, its layer, its files and the text to review. */
export default function ImportItemRow({
  row,
  levelName,
  levelIsGlobal,
  onChange,
  onToggleFile,
}: {
  row: ImportRow;
  levelName: string;
  levelIsGlobal: boolean;
  onChange: (patch: Partial<ImportRow>) => void;
  onToggleFile: (fileIndex: number, on: boolean) => void;
}) {
  const [showText, setShowText] = useState(false);
  const blocked = row.blocked !== null;
  const layer = levelIsGlobal ? "level" : row.layer;
  const layerName = layer === "global" ? "Global" : levelName;
  return (
    <li className={row.include ? "import-item" : "import-item import-off"}>
      <div className="form-row">
        <input
          type="checkbox"
          checked={row.include}
          disabled={blocked}
          aria-label={`Import ${row.source}`}
          onChange={(event) => onChange({ include: event.target.checked })}
        />
        <span className="mono small import-source" title={row.source}>
          {row.source}
        </span>
        {row.tool && <span className="import-tool">{TOOL_LABEL[row.tool]}</span>}
      </div>
      {row.blocked !== null && <p className="form-error">{row.blocked}</p>}
      {!blocked && (
        <>
          <div className="form-row">
            <select value={row.kind} aria-label="Kind" onChange={(event) => onChange({ kind: event.target.value as ItemKind })}>
              {KINDS.map((section) => (
                <option key={section.kind} value={section.kind}>
                  {section.title}
                </option>
              ))}
            </select>
            {row.kind !== "instructions" && (
              <input value={row.name} aria-label="Name" onChange={(event) => onChange({ name: event.target.value.toLowerCase() })} />
            )}
            {!levelIsGlobal && (
              <select
                value={row.layer}
                aria-label="Write to"
                onChange={(event) => onChange({ layer: event.target.value === "global" ? "global" : "level" })}
              >
                <option value="global">Global</option>
                <option value="level">{levelName}</option>
              </select>
            )}
          </div>
          <input
            className="import-summary"
            value={row.summary}
            placeholder="Summary"
            aria-label="Summary"
            onChange={(event) => onChange({ summary: event.target.value })}
          />
          {(row.kind === "standard" || row.kind === "engineering") && (
            <textarea
              className="import-rules"
              rows={Math.min(Math.max(row.rules.length, 2), 8)}
              value={row.rules.join("\n")}
              placeholder="One rule per line"
              aria-label="Rules"
              onChange={(event) =>
                onChange({ rules: event.target.value.split("\n").filter((rule, at, all) => rule.trim() || at === all.length - 1) })
              }
            />
          )}
          {row.kind === "skill" && row.files.length > 0 && (
            <ul className="import-files">
              {row.files.map((file, at) => (
                <li key={file.path}>
                  <label>
                    <input type="checkbox" checked={row.fileOn[at] ?? false} onChange={(event) => onToggleFile(at, event.target.checked)} />
                    <span className="mono small">{file.path}</span>
                    {file.script && <span className="faint small"> · script: only imported when ticked</span>}
                  </label>
                </li>
              ))}
            </ul>
          )}
          <ReviewWarnings warnings={row.warnings} />
          {row.notes.length > 0 && (
            <ul className="import-notes faint small">
              {row.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
          <p className="faint small">
            {pathFor(row.kind, row.name)}
            {row.proposed_by_model ? " · proposed by the utility model" : ""}
            {replacesExisting(row, layer) && <span className="import-warning"> · replaces the existing item in {layerName}</span>}
            {" · "}
            <button type="button" className="link-button" onClick={() => setShowText(!showText)}>
              {showText ? "Hide text" : "Show text"}
            </button>
          </p>
          {showText && (
            <pre className="import-preview">
              <MarkedText text={row.body_marked} />
            </pre>
          )}
        </>
      )}
    </li>
  );
}
