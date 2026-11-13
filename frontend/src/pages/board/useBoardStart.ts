import { useState } from "react";

import type { CardCreate, CardDetail } from "./boardApi";
import type { BoardHandle } from "./useBoard";

export interface BoardStart {
  /** Whether the storage choice is shown. */
  choosing: boolean;
  /** The card noted before the board started; it is added once the board starts. */
  pending: CardCreate | null;
  choose: () => void;
  cancel: () => void;
  /** Add a card, or ask where the board is kept first when it has not started yet. */
  addCard: (body: CardCreate) => Promise<CardDetail | null>;
  /** After the board started: add the pending card, if any. */
  started: () => Promise<void>;
}

/** The board starts at its first write: the storage choice comes up then, or from the header's link. */
export function useBoardStart(handle: BoardHandle): BoardStart {
  const [choosing, setChoosing] = useState(false);
  const [pending, setPending] = useState<CardCreate | null>(null);
  const ready = handle.board?.state === "ready";

  async function addCard(body: CardCreate): Promise<CardDetail | null> {
    if (ready) return handle.addCard(body);
    setPending(body);
    setChoosing(true);
    return null;
  }

  async function started(): Promise<void> {
    setChoosing(false);
    const body = pending;
    setPending(null);
    if (body !== null) await handle.addCard(body);
  }

  return {
    choosing,
    pending,
    choose: () => setChoosing(true),
    cancel: () => {
      setChoosing(false);
      setPending(null);
    },
    addCard,
    started,
  };
}
