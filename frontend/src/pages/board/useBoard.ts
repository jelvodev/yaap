import { useCallback, useEffect, useRef, useState } from "react";

import {
  type Board,
  type BoardCard,
  type BoardProblem,
  type CardCreate,
  type CardDetail,
  type Column,
  boardCards,
  createCard,
  moveCard,
  readBoard,
  startBoard,
} from "./boardApi";

// The board has no stream of its own: while it is on screen it asks every 15 s whether its head moved.
const POLL_MS = 15_000;

export interface BoardHandle {
  board: Board | null;
  /** Every card of the board, with a move in flight already shown in its new column. */
  cards: BoardCard[];
  loading: boolean;
  /** Why the board could not be read; the last board stays shown. */
  error: string | null;
  /** A refused move or add, shown until dismissed or the next action. */
  notice: string | null;
  dismissNotice: () => void;
  reload: () => Promise<void>;
  /** Move a card by hand; it returns to its column when the backend refuses, with the reason as notice. */
  move: (card: BoardCard, column: Column) => Promise<boolean>;
  /** Start the board on the project's branch (making a repository first when asked). */
  start: (createRepository: boolean) => Promise<BoardProblem | null>;
  addCard: (body: CardCreate) => Promise<CardDetail | null>;
}

/** A project's board: read once, then polled with `since` while the page is visible; moves are optimistic. */
export function useBoard(projectId: number): BoardHandle {
  const [board, setBoard] = useState<Board | null>(null);
  const [cards, setCards] = useState<BoardCard[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const head = useRef<string | null>(null);
  // The answer of an older request must not overwrite a newer one (a poll racing a move).
  const generation = useRef(0);

  const fetchBoard = useCallback(
    async (incremental: boolean) => {
      const asked = ++generation.current;
      const answer = await readBoard(projectId, incremental ? head.current : null);
      if (asked !== generation.current) return;
      setLoading(false);
      if (!answer.ok) {
        setError(answer.problem.message);
        return;
      }
      setError(null);
      if (answer.data.unchanged) return;
      head.current = answer.data.head;
      setBoard(answer.data);
      setCards(boardCards(answer.data));
    },
    [projectId],
  );

  const reload = useCallback(() => fetchBoard(false), [fetchBoard]);

  useEffect(() => {
    head.current = null;
    setBoard(null);
    setCards([]);
    setLoading(true);
    setNotice(null);
    void fetchBoard(false);
    const poll = () => {
      if (!document.hidden) void fetchBoard(true);
    };
    const timer = window.setInterval(poll, POLL_MS);
    document.addEventListener("visibilitychange", poll);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", poll);
      generation.current += 1;
    };
  }, [fetchBoard]);

  const move = useCallback(
    async (card: BoardCard, column: Column) => {
      setNotice(null);
      const before = cards;
      // A poll answering meanwhile would show the old column again; it is ignored.
      generation.current += 1;
      setCards((current) => current.map((one) => (one.id === card.id ? { ...one, column } : one)));
      const answer = await moveCard(projectId, card.id, column, card.updated);
      if (!answer.ok) {
        setCards(before);
        setNotice(`#${card.id} stays: ${answer.problem.message}`);
        return false;
      }
      await fetchBoard(false);
      return true;
    },
    [cards, fetchBoard, projectId],
  );

  const start = useCallback(
    async (createRepository: boolean) => {
      const answer = await startBoard(projectId, createRepository);
      if (!answer.ok) return answer.problem;
      head.current = answer.data.head;
      setBoard(answer.data);
      setCards(boardCards(answer.data));
      return null;
    },
    [projectId],
  );

  const addCard = useCallback(
    async (body: CardCreate) => {
      setNotice(null);
      const answer = await createCard(projectId, body);
      if (!answer.ok) {
        setNotice(answer.problem.message);
        return null;
      }
      await fetchBoard(false);
      return answer.data;
    },
    [fetchBoard, projectId],
  );

  return {
    board,
    cards,
    loading,
    error,
    notice,
    dismissNotice: () => setNotice(null),
    reload,
    move,
    start,
    addCard,
  };
}
