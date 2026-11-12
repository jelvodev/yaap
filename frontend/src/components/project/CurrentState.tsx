import { useEffect, useState } from "react";

import { useCurrentState } from "../../pages/chat/useCurrentState";

/** The short "where it stands, what is open, next step" block at the top of a project's memory. */
export default function CurrentState({ projectId }: { projectId: number }) {
  const { state, error, save } = useCurrentState(projectId);
  const loaded = state?.text ?? "";
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // A newly loaded or saved value replaces the draft.
  useEffect(() => {
    setText(loaded);
    setSaveError(null);
  }, [loaded]);

  if (state === null) return error ? <p className="form-error small">{error}</p> : null;

  const maxChars = state.max_chars;
  const over = text.length > maxChars;
  const editable = state.has_repository;

  async function submit() {
    setBusy(true);
    setSaveError(await save(text));
    setBusy(false);
  }

  return (
    <section className="pp-current-state">
      <h3>Current state</h3>
      <p className="faint small">
        Where the work stands, what is open and the next step. Every new conversation of this project starts from it.
      </p>
      <textarea
        aria-label="Current state"
        value={text}
        rows={6}
        disabled={!editable || busy}
        onChange={(event) => setText(event.target.value)}
      />
      <div className="pp-row">
        <button
          type="button"
          className="button button-primary"
          disabled={!editable || busy || over || !text.trim() || text === loaded}
          onClick={() => void submit()}
        >
          Save
        </button>
        <button
          type="button"
          className="button button-ghost"
          disabled={!editable || busy || text === loaded}
          onClick={() => setText(loaded)}
        >
          Revert
        </button>
        <span className={over ? "pp-current-state-count form-error small" : "pp-current-state-count faint small"}>
          {text.length} of {maxChars}
        </span>
      </div>
      {!editable && <p className="faint small">The project needs a git repository for its current state.</p>}
      {saveError && <p className="form-error small">{saveError}</p>}
    </section>
  );
}
