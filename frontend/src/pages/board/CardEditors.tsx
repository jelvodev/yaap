import { type ReactNode, useState } from "react";

import { Markdown } from "../../components/message/Markdown";
import type { BoardVersion, CardDetail, CardKind } from "./boardApi";
import type { CardDetailHandle } from "./useCardDetail";

const KINDS: CardKind[] = ["bug", "feature", "refactor", "chore"];

/** Parse "3, 5" into card numbers; null when something is not a whole card number. */
export function parseWaitsFor(input: string): number[] | null {
  const parts = input
    .split(",")
    .map((part) => part.trim())
    .filter((part) => part !== "");
  const numbers = parts.map((part) => (/^\d+$/.test(part) ? Number(part) : Number.NaN));
  return numbers.some((value) => Number.isNaN(value)) ? null : numbers;
}

function isKind(value: string): value is CardKind {
  return KINDS.some((kind) => kind === value);
}

/** The title with the fields that describe the card, shown as text until "Edit". */
export function TitleEditor({
  card,
  versions,
  handle,
}: {
  card: CardDetail;
  versions: BoardVersion[];
  handle: CardDetailHandle;
}) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(card.title);
  const [kind, setKind] = useState<CardKind>(card.kind ?? "feature");
  const [version, setVersion] = useState(card.version ?? "");
  const [waits, setWaits] = useState(card.waits_for.join(", "));
  const [problem, setProblem] = useState<string | null>(null);

  function open(): void {
    setTitle(card.title);
    setKind(card.kind ?? "feature");
    setVersion(card.version ?? "");
    setWaits(card.waits_for.join(", "));
    setProblem(null);
    setEditing(true);
  }
  async function submit(): Promise<void> {
    const waitsFor = parseWaitsFor(waits);
    if (waitsFor === null) return setProblem("Waits for takes card numbers separated by commas, such as 3, 5.");
    if (title.trim() === "") return setProblem("The title cannot be empty.");
    if (await handle.save({ title: title.trim(), kind, version: version || null, waits_for: waitsFor })) {
      setEditing(false);
    }
  }

  if (!editing) {
    return (
      <div className="card-detail-title">
        <h2>{card.title}</h2>
        {!card.broken && (
          <button type="button" className="button button-ghost" onClick={open}>
            Edit
          </button>
        )}
      </div>
    );
  }
  return (
    <div className="card-detail-form">
      <label>
        Title
        <input value={title} onChange={(event) => setTitle(event.target.value)} />
      </label>
      <label>
        Kind
        <select
          value={kind}
          onChange={(event) => {
            if (isKind(event.target.value)) setKind(event.target.value);
          }}
        >
          {KINDS.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      </label>
      <label>
        Version
        <select value={version} onChange={(event) => setVersion(event.target.value)}>
          <option value="">Not scheduled</option>
          {versions.map((item) => (
            <option key={item.name} value={item.name}>
              {item.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        Waits for
        <input value={waits} placeholder="Card numbers, e.g. 3, 5" onChange={(event) => setWaits(event.target.value)} />
      </label>
      {problem && <div className="form-error">{problem}</div>}
      <div className="card-detail-actions">
        <button type="button" className="button button-primary" disabled={handle.busy} onClick={() => void submit()}>
          Save
        </button>
        <button type="button" className="button button-ghost" onClick={() => setEditing(false)}>
          Cancel
        </button>
      </div>
    </div>
  );
}

/** A block of text that is read as Markdown and edited as plain text in a textarea. */
export function TextEditor({
  heading,
  value,
  plain,
  empty,
  busy,
  onSave,
  children,
}: {
  heading: string;
  value: string;
  plain: boolean;
  empty: string;
  busy: boolean;
  onSave: (text: string) => Promise<boolean>;
  /** Replaces the Markdown view (the checklist of the definition of done). */
  children?: ReactNode;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  async function submit(): Promise<void> {
    if (await onSave(draft)) setEditing(false);
  }
  function view(): ReactNode {
    if (children) return children;
    return value.trim() ? <Markdown text={value} plain={plain} /> : <p className="faint">{empty}</p>;
  }
  return (
    <section className="card-detail-section">
      <h3>
        {heading}
        {!editing && (
          <button
            type="button"
            className="button button-ghost"
            onClick={() => {
              setDraft(value);
              setEditing(true);
            }}
          >
            Edit
          </button>
        )}
      </h3>
      {editing ? (
        <>
          <textarea rows={8} value={draft} aria-label={heading} onChange={(event) => setDraft(event.target.value)} />
          <div className="card-detail-actions">
            <button type="button" className="button button-primary" disabled={busy} onClick={() => void submit()}>
              Save
            </button>
            <button type="button" className="button button-ghost" onClick={() => setEditing(false)}>
              Cancel
            </button>
          </div>
        </>
      ) : (
        view()
      )}
    </section>
  );
}
