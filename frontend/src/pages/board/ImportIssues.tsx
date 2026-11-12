import { useEffect, useState } from "react";

import { api } from "../../api/client";
import type { components } from "../../api/schema";
import "./card-detail.css";
import { boardProblem } from "./boardApi";

type ImportOut = components["schemas"]["IssueImportOut"];
type Way = "numbers" | "label";

const MAX_NUMBERS = 50;
const SKIP_REASONS: Record<ImportOut["skipped"][number]["reason"], string> = {
  imported: "imported before",
  pull_request: "a pull request",
  not_found: "not found",
  limit: "Inbox is full",
};

/** Issue numbers from text like "12, 15 #20"; null when something else is in it or there are too many. */
export function parseNumbers(text: string): number[] | null {
  const parts = text.split(/[\s,]+/).filter((part) => part !== "");
  const numbers = parts.map((part) => Number(part.replace(/^#/, "")));
  if (numbers.length === 0 || numbers.length > MAX_NUMBERS) return null;
  return numbers.every((value) => Number.isInteger(value) && value > 0)
    ? numbers
    : null;
}

function summary(result: ImportOut): string {
  const added =
    result.added.length === 1
      ? "1 card added"
      : `${result.added.length} cards added`;
  const skipped = result.skipped.map(
    (item) => `#${item.number} ${SKIP_REASONS[item.reason]}`,
  );
  return skipped.length > 0
    ? `${added}; skipped: ${skipped.join(", ")}.`
    : `${added}.`;
}

interface DialogProps {
  projectId: number;
  onClose: () => void;
  onImported: () => void;
}

function ImportDialog({ projectId, onClose, onImported }: DialogProps) {
  const [way, setWay] = useState<Way>("numbers");
  const [numbers, setNumbers] = useState("");
  const [label, setLabel] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    function onKey(event: KeyboardEvent): void {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function submit(): Promise<void> {
    const chosen = way === "numbers" ? parseNumbers(numbers) : null;
    if (way === "numbers" && chosen === null) {
      return setProblem(
        `Give 1 to ${MAX_NUMBERS} issue numbers, separated by commas or spaces.`,
      );
    }
    if (way === "label" && label.trim() === "")
      return setProblem("Give a label.");
    setBusy(true);
    setProblem(null);
    const { data, error } = await api.POST(
      "/api/v1/projects/{project_id}/board/import-issues",
      {
        params: { path: { project_id: projectId } },
        body: chosen !== null ? { numbers: chosen } : { label: label.trim() },
      },
    );
    setBusy(false);
    if (!data)
      return setProblem(
        boardProblem(error, "The issues could not be imported.").message,
      );
    setDone(summary(data));
    if (data.added.length > 0) onImported();
  }

  return (
    <div className="palette-backdrop confirm-backdrop" onMouseDown={onClose}>
      <div
        className="palette version-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Import issues"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <h2>Import issues</h2>
        <p className="faint small">
          Issues of the project's GitHub repository become cards in Inbox.
          Nothing is written to GitHub; an issue imported before is skipped.
        </p>
        <label>
          Import
          <select
            value={way}
            onChange={(event) =>
              setWay(event.target.value === "label" ? "label" : "numbers")
            }
          >
            <option value="numbers">By number</option>
            <option value="label">Open issues with a label</option>
          </select>
        </label>
        {way === "numbers" ? (
          <label>
            Issue numbers
            <input
              value={numbers}
              autoFocus
              placeholder="12, 15, 20"
              onChange={(event) => setNumbers(event.target.value)}
            />
          </label>
        ) : (
          <label>
            Label
            <input
              value={label}
              autoFocus
              maxLength={100}
              onChange={(event) => setLabel(event.target.value)}
            />
          </label>
        )}
        {problem && <div className="form-error">{problem}</div>}
        {done && (
          <p className="small" role="status">
            {done}
          </p>
        )}
        <div className="confirm-dialog-actions">
          <button
            type="button"
            className="button button-ghost"
            onClick={onClose}
          >
            {done ? "Close" : "Cancel"}
          </button>
          <button
            type="button"
            className="button button-primary"
            disabled={busy}
            onClick={() => void submit()}
          >
            Import
          </button>
        </div>
      </div>
    </div>
  );
}

/** *Import issues* in Inbox: a button that opens the dialog. */
export default function ImportIssues({
  projectId,
  onImported,
}: {
  projectId: number;
  onImported: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className="board-add" onClick={() => setOpen(true)}>
        Import issues
      </button>
      {open && (
        <ImportDialog
          projectId={projectId}
          onClose={() => setOpen(false)}
          onImported={onImported}
        />
      )}
    </>
  );
}
