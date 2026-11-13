import { useRef, useState } from "react";

import { useDismiss } from "../../components/useDismiss";
import { useBoardLinks } from "../chat/useChatLinks";
import type { BoardCard, Column } from "./boardApi";
import { COLUMNS, COLUMN_LABELS, canDrop, cardState, originLabel } from "./boardLogic";

export interface TileProps {
  card: BoardCard;
  projectId: number;
  selected: boolean;
  onMove: (card: BoardCard, column: Column) => void;
  onDragStart?: (card: BoardCard) => void;
  onDragEnd?: () => void;
}

function MoveMenu({ card, onMove }: { card: BoardCard; onMove: (card: BoardCard, column: Column) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useDismiss(ref, open, () => setOpen(false));
  return (
    <div className="board-menu" ref={ref} onClick={(event) => event.stopPropagation()}>
      <button
        type="button"
        className="board-menu-button"
        aria-label={`Actions for card ${card.id}`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        onKeyDown={(event) => event.stopPropagation()}
      >
        ⋯
      </button>
      {open && (
        <div className="board-menu-list" role="menu" onKeyDown={(event) => event.stopPropagation()}>
          <div className="board-menu-title faint small">Move to…</div>
          {COLUMNS.filter((column) => column !== card.column).map((column) => {
            const check = canDrop(card, column);
            return (
              <button
                key={column}
                type="button"
                role="menuitem"
                disabled={!check.ok}
                title={check.ok ? undefined : check.reason}
                onClick={() => {
                  setOpen(false);
                  onMove(card, column);
                }}
              >
                {COLUMN_LABELS[column]}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function Tags({ card }: { card: BoardCard }) {
  const origin = originLabel(card.origin.type);
  return (
    <div className="board-tags">
      {card.kind && <span className={`board-tag board-kind-${card.kind}`}>{card.kind}</span>}
      <span className="board-tag">{card.version ?? "not scheduled"}</span>
      {origin && <span className="board-tag board-tag-origin">{origin}</span>}
      {card.blocked_by.map((id) => (
        <span key={id} className="board-tag board-tag-blocked">
          after #{id}
        </span>
      ))}
      {card.open_questions > 0 && <span className="board-tag board-tag-question">? question</span>}
    </div>
  );
}

/** One card on the board: opens on click or Enter, drags between columns, and has a Move to… menu. */
export default function CardTile({ card, projectId, selected, onMove, onDragStart, onDragEnd }: TileProps) {
  const { openCard } = useBoardLinks();
  const state = cardState(card);
  const classes = ["board-tile", `board-state-${state.tone}`, selected ? "selected" : ""].filter(Boolean).join(" ");
  const open = () => openCard(projectId, card.id);
  return (
    <div
      role="button"
      tabIndex={0}
      className={classes}
      draggable={!card.broken}
      onClick={open}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          open();
        }
      }}
      onDragStart={(event) => {
        event.dataTransfer.setData("text/plain", String(card.id));
        event.dataTransfer.effectAllowed = "move";
        onDragStart?.(card);
      }}
      onDragEnd={() => onDragEnd?.()}
    >
      <div className="board-tile-head">
        <span className="mono faint board-tile-id">#{card.id}</span>
        <MoveMenu card={card} onMove={onMove} />
      </div>
      <div className="board-tile-title">{card.title}</div>
      <Tags card={card} />
      {state.label && (
        <div className="board-tile-state">
          <span className="board-dot" />
          {state.label}
          {card.next.label && <span className="faint"> · {card.next.label}</span>}
        </div>
      )}
      {!state.label && card.next.label && <div className="board-tile-state faint">{card.next.label}</div>}
      {card.cost_usd > 0 && <div className="board-tile-foot faint mono">${card.cost_usd.toFixed(2)}</div>}
    </div>
  );
}
