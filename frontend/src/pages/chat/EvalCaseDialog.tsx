import { useEffect } from "react";

import EvalCheckEditor from "./EvalCheckEditor";
import { type EvalCaseDraft, type EvalCaseDraftState, useEvalCaseDraft } from "./useEvalCaseDraft";

type Budget = NonNullable<EvalCaseDraft["budget"]>;

/** A number field's value, or null when it is empty. */
function numberOrNull(text: string): number | null {
  if (!text.trim()) return null;
  const value = Number(text);
  return Number.isFinite(value) ? value : null;
}

/**
 * Save as eval case: the proposal made from the conversation, to edit before it is written to the
 * project's `.blueprint/evals/<name>.md` as one commit.
 */
export default function EvalCaseDialog({
  conversationId,
  projectId,
  onClose,
}: {
  conversationId: number;
  projectId: number;
  onClose: () => void;
}) {
  const state = useEvalCaseDraft(conversationId, projectId);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="palette-backdrop" onMouseDown={onClose}>
      <div
        className="palette import-dialog eval-case-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Save as eval case"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <h2>Save as eval case</h2>
        {state.saved ? (
          <>
            <p>
              Saved as <code>{state.saved.path}</code> in commit <code>{state.saved.commit.slice(0, 10)}</code>.
            </p>
            <div className="import-row import-actions">
              <button type="button" className="button button-primary" onClick={onClose}>
                Close
              </button>
            </div>
          </>
        ) : (
          <DraftForm state={state} onClose={onClose} />
        )}
      </div>
    </div>
  );
}

function DraftForm({ state, onClose }: { state: EvalCaseDraftState; onClose: () => void }) {
  const { draft, proposal } = state;
  if (state.loading) return <p className="faint small">Reading the conversation and proposing checks…</p>;
  if (!draft || !proposal) {
    return (
      <>
        {state.error && <p className="form-error small">{state.error}</p>}
        <div className="import-row import-actions">
          <button type="button" className="button button-ghost" onClick={onClose}>
            Close
          </button>
        </div>
      </>
    );
  }
  const budget: Budget = draft.budget ?? {};
  const setBudget = (patch: Partial<Budget>) => state.change({ budget: { ...budget, ...patch } });
  return (
    <form
      className="eval-case-form"
      onSubmit={(event) => {
        event.preventDefault();
        void state.save();
      }}
    >
      <p className="faint small">
        The agent gets the prompt in a fresh copy of the project at the start, and the checks decide whether its work
        is as good as this conversation's.
        {proposal.checks_from === "model" ? " The utility model proposed the checks." : ""}
      </p>
      {proposal.notes.map((note) => (
        <p key={note} className="small eval-case-note">
          {note}
        </p>
      ))}
      <label className="import-field">
        <span>Name: lowercase letters, digits and hyphens</span>
        <input className="mono" value={draft.name} maxLength={60} onChange={(event) => state.change({ name: event.target.value })} />
      </label>
      <label className="import-field">
        <span>Summary (optional)</span>
        <input value={draft.summary ?? ""} maxLength={300} onChange={(event) => state.change({ summary: event.target.value })} />
      </label>
      <label className="import-field">
        <span>Prompt</span>
        <textarea rows={5} value={draft.prompt} onChange={(event) => state.change({ prompt: event.target.value })} />
      </label>
      <StartFields state={state} draft={draft} />
      <fieldset className="eval-case-budget">
        <legend>Budget (optional; twice what this conversation used)</legend>
        <BudgetField label="Steps" value={budget.steps} onChange={(steps) => setBudget({ steps })} />
        <BudgetField label="Tokens" value={budget.tokens} onChange={(tokens) => setBudget({ tokens })} />
        <BudgetField label="Cost (USD)" step="0.01" value={budget.cost_usd} onChange={(cost_usd) => setBudget({ cost_usd })} />
        <BudgetField
          label="Time (min)"
          step="0.5"
          value={budget.seconds != null ? budget.seconds / 60 : null}
          onChange={(minutes) => setBudget({ seconds: minutes != null ? Math.round(minutes * 60) : null })}
        />
      </fieldset>
      <h3 className="eval-case-heading">Checks</h3>
      {proposal.test_command === null && <p className="faint small">No test command was found in the project.</p>}
      {draft.checks.map((check, index) => (
        <EvalCheckEditor
          key={index}
          check={check}
          index={index}
          onChange={(changed) => state.changeCheck(index, changed)}
          onRemove={() => state.removeCheck(index)}
        />
      ))}
      <div className="import-row">
        <span className="faint small">Add a check:</span>
        <button type="button" className="button button-ghost" onClick={() => state.addCheck("command")}>
          Command
        </button>
        <button type="button" className="button button-ghost" onClick={() => state.addCheck("file")}>
          File
        </button>
        <button type="button" className="button button-ghost" onClick={() => state.addCheck("rubric")}>
          Rubric
        </button>
      </div>
      {state.error && <p className="form-error small">{state.error}</p>}
      <div className="import-row import-actions">
        <button type="button" className="button button-ghost" onClick={onClose}>
          Cancel
        </button>
        <button type="submit" className="button button-primary" disabled={state.saving || !draft.name.trim() || !draft.prompt.trim()}>
          {state.saving ? "Saving…" : "Save case"}
        </button>
      </div>
    </form>
  );
}

function StartFields({ state, draft }: { state: EvalCaseDraftState; draft: EvalCaseDraft }) {
  const first = state.proposal?.first_checkpoint ?? null;
  return (
    <fieldset className="eval-case-start" role="radiogroup" aria-label="Where the case starts">
      <legend>Start</legend>
      <label>
        <input type="radio" name="eval-start" checked={state.startKind === "commit"} onChange={() => state.setStartKind("commit")} />
        Commit
        <input
          className="mono"
          aria-label="Start commit"
          value={draft.start.commit ?? ""}
          placeholder="the project's commit hash"
          disabled={state.startKind !== "commit"}
          onChange={(event) => state.change({ start: { ...draft.start, commit: event.target.value } })}
        />
      </label>
      {first !== null && (
        <label>
          <input
            type="radio"
            name="eval-start"
            checked={state.startKind === "checkpoint"}
            onChange={() => {
              state.setStartKind("checkpoint");
              state.change({ start: { ...draft.start, checkpoint: draft.start.checkpoint ?? first } });
            }}
          />
          The conversation&apos;s first checkpoint (#{draft.start.checkpoint ?? first}, kept for the case)
        </label>
      )}
    </fieldset>
  );
}

function BudgetField({
  label,
  value,
  step = "1",
  onChange,
}: {
  label: string;
  value: number | null | undefined;
  step?: string;
  onChange: (value: number | null) => void;
}) {
  return (
    <label className="import-field">
      <span>{label}</span>
      <input type="number" min="0" step={step} value={value ?? ""} onChange={(event) => onChange(numberOrNull(event.target.value))} />
    </label>
  );
}
