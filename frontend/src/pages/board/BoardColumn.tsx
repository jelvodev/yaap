import { useState } from "react";

import type { BoardCard, CardCreate, Column } from "./boardApi";
import { COLUMN_LABELS, COLUMN_NOTES } from "./boardLogic";
import CardTile from "./CardTile";

/** The body for a card noted by title only. */
export function newCard(title: string, column: "inbox" | "idea", version: string | null = null): CardCreate {
  return { title, column, kind: "feature", description: "", version };
}

/** What a column or section needs from the page; shared by the board and the list. */
export interface ColumnActions {
  projectId: number;
  selectedId: number | null;
  doneOpen: boolean;
  setDoneOpen: (open: boolean) => void;
  onMove: (card: BoardCard, column: Column) => void;
  onAdd: (title: string, column: "inbox" | "idea") => Promise<void>;
  onDragStart: (card: BoardCard) => void;
  onDragEnd: () => void;
  /** Dropping the dragged card here; the page checks the move. */
  onDrop: (column: Column) => void;
  dragging: BoardCard | null;
}

export interface ColumnData {
  column: Column;
  cards: BoardCard[];
  /** The server's count; it can exceed the cards shown. */
  count: number;
  truncated: boolean;
}

/** The "+ Add card" line of Inbox and Idea: an inline input, Enter or Add saves, Escape cancels. */
export function AddCard({ column, onAdd }: { column: "inbox" | "idea"; onAdd: ColumnActions["onAdd"] }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  async function submit() {
    const text = title.trim();
    if (!text) return;
    await onAdd(text, column);
    setTitle("");
    setOpen(false);
  }
  if (!open) {
    return (
      <button type="button" className="board-add" onClick={() => setOpen(true)}>
        + Add card
      </button>
    );
  }
  return (
    <div className="board-add-form">
      <input
        autoFocus
        value={title}
        placeholder="Title"
        aria-label={`New card in ${COLUMN_LABELS[column]}`}
        onChange={(event) => setTitle(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") void submit();
          if (event.key === "Escape") {
            setTitle("");
            setOpen(false);
          }
        }}
      />
      <button
        type="button"
        className="button button-primary board-small"
        disabled={!title.trim()}
        onClick={() => void submit()}
      >
        Add
      </button>
    </div>
  );
}

/** Whether a column in the list or board is a folded Done. */
export function isFolded(column: Column, doneOpen: boolean): boolean {
  return column === "done" && !doneOpen;
}

/** One column of the board. */
export default function BoardColumn({ data, actions }: { data: ColumnData; actions: ColumnActions }) {
  const [over, setOver] = useState(false);
  const { column } = data;
  const label = COLUMN_LABELS[column];
  const droppable = actions.dragging !== null && actions.dragging.column !== column;
  const dropHandlers = {
    onDragOver: (event: React.DragEvent) => {
      if (!droppable) return;
      event.preventDefault();
      setOver(true);
    },
    onDragLeave: () => setOver(false),
    onDrop: (event: React.DragEvent) => {
      event.preventDefault();
      setOver(false);
      if (droppable) actions.onDrop(column);
    },
  };

  if (isFolded(column, actions.doneOpen)) {
    return (
      <button
        type="button"
        className={`board-column board-column-folded${over ? " over" : ""}`}
        aria-label={`${label}, ${data.count} cards. Unfold`}
        onClick={() => actions.setDoneOpen(true)}
        {...dropHandlers}
      >
        <span className="board-folded-name">{label}</span>
        <span className="board-count">{data.count}</span>
      </button>
    );
  }

  return (
    <section className={`board-column${over ? " over" : ""}`} aria-label={label} {...dropHandlers}>
      <header className="board-column-head">
        <span className="board-column-name">{label}</span>
        <span className="board-count">
          {data.truncated ? `${data.cards.length} shown of ${data.count}` : data.count}
        </span>
        {column === "done" && (
          <button
            type="button"
            className="board-fold"
            aria-label="Fold Done"
            onClick={() => actions.setDoneOpen(false)}
          >
            ‹
          </button>
        )}
      </header>
      {COLUMN_NOTES[column] && <p className="board-column-note faint small">{COLUMN_NOTES[column]}</p>}
      <div className="board-tiles">
        {data.cards.map((card) => (
          <CardTile
            key={card.id}
            card={card}
            projectId={actions.projectId}
            selected={card.id === actions.selectedId}
            onMove={actions.onMove}
            onDragStart={actions.onDragStart}
            onDragEnd={actions.onDragEnd}
          />
        ))}
        {(column === "inbox" || column === "idea") && <AddCard column={column} onAdd={actions.onAdd} />}
      </div>
    </section>
  );
}
