import { useState } from "react";

import { checkLine, moved, nonEmptyLines, parseCheckLine } from "./planProgress";
import type { PlanEdit, PlanOut } from "./usePlan";

interface DraftStep {
  key: number;
  title: string;
  details: string;
  files: string;
  checks: string;
}

let nextKey = 0;

function draftStep(step?: PlanOut["steps"][number]): DraftStep {
  nextKey += 1;
  return {
    key: nextKey,
    title: step?.title ?? "",
    details: step?.details ?? "",
    files: (step?.files ?? []).join("\n"),
    checks: (step?.checks ?? []).map(checkLine).join("\n"),
  };
}

function toEdit(title: string, summary: string, risks: string, steps: DraftStep[]): PlanEdit {
  return {
    title: title.trim(),
    summary: summary.trim(),
    risks: nonEmptyLines(risks),
    steps: steps.map((step) => ({
      title: step.title.trim(),
      details: step.details.trim(),
      files: nonEmptyLines(step.files),
      checks: nonEmptyLines(step.checks).map(parseCheckLine),
    })),
  };
}

/**
 * The inline editor of a draft plan: reword, reorder, delete and add steps. Files and checks are one per line; a
 * check's command comes after `run:`.
 */
export default function PlanEditor({
  plan,
  busy,
  invalid,
  onSave,
  onCancel,
}: {
  plan: PlanOut;
  busy: boolean;
  invalid: string[];
  onSave: (edit: PlanEdit) => void;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState(plan.title);
  const [summary, setSummary] = useState(plan.summary);
  const [risks, setRisks] = useState(plan.risks.join("\n"));
  const [steps, setSteps] = useState<DraftStep[]>(() => plan.steps.map(draftStep));

  function change(index: number, field: keyof Omit<DraftStep, "key">, value: string) {
    setSteps((current) => current.map((step, position) => (position === index ? { ...step, [field]: value } : step)));
  }

  return (
    <form
      className="plan-editor"
      onSubmit={(event) => {
        event.preventDefault();
        onSave(toEdit(title, summary, risks, steps));
      }}
    >
      <label className="plan-field">
        <span>Title</span>
        <input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={200} required />
      </label>
      <label className="plan-field">
        <span>Summary</span>
        <textarea value={summary} onChange={(event) => setSummary(event.target.value)} rows={3} />
      </label>
      <ol className="plan-editor-steps">
        {steps.map((step, index) => (
          <li key={step.key} className="plan-editor-step">
            <div className="plan-editor-step-head">
              <span className="plan-step-number">{index + 1}</span>
              <input
                aria-label={`Step ${index + 1} title`}
                value={step.title}
                onChange={(event) => change(index, "title", event.target.value)}
                maxLength={200}
              />
              <div className="plan-editor-step-actions">
                <button
                  type="button"
                  className="link-button"
                  disabled={index === 0}
                  onClick={() => setSteps((current) => moved(current, index, -1))}
                >
                  Up
                </button>
                <button
                  type="button"
                  className="link-button"
                  disabled={index === steps.length - 1}
                  onClick={() => setSteps((current) => moved(current, index, 1))}
                >
                  Down
                </button>
                <button
                  type="button"
                  className="link-button danger-link"
                  onClick={() => setSteps((current) => current.filter((_, position) => position !== index))}
                >
                  Delete
                </button>
              </div>
            </div>
            <label className="plan-field">
              <span>Details</span>
              <textarea
                value={step.details}
                onChange={(event) => change(index, "details", event.target.value)}
                rows={3}
              />
            </label>
            <label className="plan-field">
              <span>Files, one per line</span>
              <textarea
                className="plan-mono"
                value={step.files}
                onChange={(event) => change(index, "files", event.target.value)}
                rows={2}
              />
            </label>
            <label className="plan-field">
              <span>Checks, one per line; a command after run:</span>
              <textarea
                className="plan-mono"
                value={step.checks}
                onChange={(event) => change(index, "checks", event.target.value)}
                rows={2}
              />
            </label>
          </li>
        ))}
      </ol>
      <button type="button" className="link-button" onClick={() => setSteps((current) => [...current, draftStep()])}>
        Add a step
      </button>
      <label className="plan-field">
        <span>Risks, one per line</span>
        <textarea value={risks} onChange={(event) => setRisks(event.target.value)} rows={2} />
      </label>
      {invalid.length > 0 && (
        <ul className="plan-errors" role="alert">
          {invalid.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}
      <div className="plan-actions">
        <button type="submit" className="button button-primary" disabled={busy}>
          Save
        </button>
        <button type="button" className="button" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
