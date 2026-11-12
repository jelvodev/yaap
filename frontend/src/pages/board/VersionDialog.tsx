import { useEffect, useState } from "react";

import "./card-detail.css";
import { type BoardVersion, putVersion } from "./boardApi";

interface Props {
  projectId: number;
  /** The version to edit, or null to make a new one. */
  version: BoardVersion | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}

type VersionState = "open" | "released";

/** The name rules of the backend; the server decides, this spares a request. */
export function nameProblem(name: string): string | null {
  if (name.trim() === "") return "Give the version a name.";
  if (name.length > 40) return "The name can have at most 40 characters.";
  if (name.includes("/")) return "The name cannot contain a slash.";
  return null;
}

/** Budget text to a number: empty is no budget; null when it is not a number of zero or more. */
export function parseBudget(text: string): number | null | undefined {
  if (text.trim() === "") return null;
  const value = Number(text);
  return Number.isFinite(value) && value >= 0 ? value : undefined;
}

export default function VersionDialog({ projectId, version, onClose, onSaved }: Props) {
  const [name, setName] = useState(version?.name ?? "");
  const [state, setState] = useState<VersionState>(version?.state ?? "open");
  const [budget, setBudget] = useState(version?.budget_usd == null ? "" : String(version.budget_usd));
  const [notes, setNotes] = useState(version?.notes ?? "");
  const [criteria, setCriteria] = useState(version?.acceptance_criteria ?? "");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    function onKey(event: KeyboardEvent): void {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function submit(): Promise<void> {
    const wrongName = version ? null : nameProblem(name);
    const budgetUsd = parseBudget(budget);
    if (wrongName) return setProblem(wrongName);
    if (budgetUsd === undefined) return setProblem("The budget is a number of dollars, or empty for none.");
    setBusy(true);
    setProblem(null);
    const answer = await putVersion(projectId, version?.name ?? name.trim(), {
      state,
      budget_usd: budgetUsd,
      notes,
      acceptance_criteria: criteria,
    });
    setBusy(false);
    if (!answer.ok) return setProblem(answer.problem.message);
    await onSaved();
    onClose();
  }

  return (
    <div className="palette-backdrop confirm-backdrop" onMouseDown={onClose}>
      <div
        className="palette version-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={version ? `Version ${version.name}` : "New version"}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <h2>{version ? "Version" : "New version"}</h2>
        {version?.error && <div className="form-error">{version.error}</div>}
        <label>
          Name
          {version ? (
            <span className="mono">{version.name}</span>
          ) : (
            <input value={name} maxLength={40} autoFocus onChange={(event) => setName(event.target.value)} />
          )}
        </label>
        <label>
          State
          <select
            value={state}
            onChange={(event) => setState(event.target.value === "released" ? "released" : "open")}
          >
            <option value="open">Open</option>
            <option value="released">Released</option>
          </select>
        </label>
        <label>
          Budget in USD
          <input
            type="number"
            min="0"
            step="any"
            value={budget}
            placeholder="No budget"
            onChange={(event) => setBudget(event.target.value)}
          />
        </label>
        <label>
          Notes
          <textarea rows={3} value={notes} onChange={(event) => setNotes(event.target.value)} />
        </label>
        <label>
          Acceptance criteria
          <textarea rows={4} value={criteria} onChange={(event) => setCriteria(event.target.value)} />
          <span className="faint small">One criterion per line; a line may carry a check YAAP runs.</span>
        </label>
        {problem && <div className="form-error">{problem}</div>}
        <div className="confirm-dialog-actions">
          <button type="button" className="button button-ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="button button-primary" disabled={busy} onClick={() => void submit()}>
            Save
          </button>
        </div>
      </div>
    </div>
  );
}
