import { useCallback, useState } from "react";

import type { BoardCard, Column } from "./boardApi";
import { canDrop } from "./boardLogic";
import type { BoardHandle } from "./useBoard";

export interface BoardDnd {
  dragging: BoardCard | null;
  /** The sentence for a drop or move the board itself refuses; the backend's refusals come through `board.notice`. */
  notice: string | null;
  dismiss: () => void;
  start: (card: BoardCard) => void;
  end: () => void;
  /** Move a card by hand (drop or menu): refused moves show their reason without a request. */
  moveTo: (card: BoardCard, column: Column) => void;
  drop: (column: Column) => void;
}

/** Dragging a card and moving it by hand, on top of the board's own optimistic `move`. */
export function useBoardDnd(board: BoardHandle): BoardDnd {
  const [dragging, setDragging] = useState<BoardCard | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const { move, dismissNotice } = board;

  const moveTo = useCallback(
    (card: BoardCard, column: Column) => {
      dismissNotice();
      const check = canDrop(card, column);
      if (!check.ok) {
        setNotice(`#${card.id} stays: ${check.reason}`);
        return;
      }
      setNotice(null);
      void move(card, column);
    },
    [dismissNotice, move],
  );

  const drop = useCallback(
    (column: Column) => {
      if (dragging !== null) moveTo(dragging, column);
      setDragging(null);
    },
    [dragging, moveTo],
  );

  return {
    dragging,
    notice,
    dismiss: () => {
      setNotice(null);
      dismissNotice();
    },
    start: setDragging,
    end: () => setDragging(null),
    moveTo,
    drop,
  };
}
