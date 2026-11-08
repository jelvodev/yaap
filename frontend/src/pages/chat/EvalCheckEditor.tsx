import type { EvalCheck } from "./useEvalCaseDraft";

const KIND_LABELS: Record<EvalCheck["kind"], string> = {
  command: "Command must exit with 0",
  file: "File content",
  rubric: "Rubric scored by a model",
};

interface EditorProps {
  check: EvalCheck;
  index: number;
  onChange: (check: EvalCheck) => void;
  onRemove: () => void;
}

/** One check of an eval case, in the fields of its kind. */
export default function EvalCheckEditor({ check, index, onChange, onRemove }: EditorProps) {
  const id = `eval-check-${index}`;
  return (
    <fieldset className="eval-check">
      <legend>
        {KIND_LABELS[check.kind]}
        <button type="button" className="button button-ghost" onClick={onRemove}>
          Remove
        </button>
      </legend>
      <label className="import-field">
        <span>Name (optional)</span>
        <input id={`${id}-name`} value={check.name} onChange={(event) => onChange({ ...check, name: event.target.value })} />
      </label>
      {check.kind === "command" && (
        <label className="import-field">
          <span>Command</span>
          <input className="mono" value={check.run} onChange={(event) => onChange({ ...check, run: event.target.value })} />
        </label>
      )}
      {check.kind === "file" && <FileFields check={check} onChange={onChange} />}
      {check.kind === "rubric" && (
        <>
          <label className="import-field">
            <span>What a good outcome is</span>
            <textarea rows={2} value={check.rubric} onChange={(event) => onChange({ ...check, rubric: event.target.value })} />
          </label>
          <ListField
            label="Files shown to the scorer, one per line"
            value={check.files ?? []}
            onChange={(files) => onChange({ ...check, files })}
          />
        </>
      )}
    </fieldset>
  );
}

type FileCheck = Extract<EvalCheck, { kind: "file" }>;

function FileFields({ check, onChange }: { check: FileCheck; onChange: (check: EvalCheck) => void }) {
  return (
    <>
      <label className="import-field">
        <span>Path in the project</span>
        <input className="mono" value={check.path} onChange={(event) => onChange({ ...check, path: event.target.value })} />
      </label>
      <label className="eval-check-toggle">
        <input
          type="checkbox"
          checked={check.exists === false}
          onChange={(event) => onChange({ ...check, exists: !event.target.checked })}
        />
        The file must be gone
      </label>
      {check.exists !== false && (
        <>
          <ListField label="Must contain, one per line" value={check.contains ?? []} onChange={(contains) => onChange({ ...check, contains })} />
          <ListField
            label="Must not contain, one per line"
            value={check.not_contains ?? []}
            onChange={(not_contains) => onChange({ ...check, not_contains })}
          />
          <ListField
            label="Must match (regular expressions), one per line"
            value={check.matches ?? []}
            onChange={(matches) => onChange({ ...check, matches })}
          />
          <ListField
            label="Must not match (regular expressions), one per line"
            value={check.not_matches ?? []}
            onChange={(not_matches) => onChange({ ...check, not_matches })}
          />
        </>
      )}
    </>
  );
}

function ListField({ label, value, onChange }: { label: string; value: string[]; onChange: (value: string[]) => void }) {
  return (
    <label className="import-field">
      <span>{label}</span>
      <textarea
        className="mono"
        rows={Math.min(Math.max(value.length, 1), 4)}
        value={value.join("\n")}
        onChange={(event) => onChange(event.target.value.split("\n"))}
      />
    </label>
  );
}
