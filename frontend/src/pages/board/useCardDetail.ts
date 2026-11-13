import { useCallback, useEffect, useState } from "react";

import {
  type BoardResult,
  type CardDetail,
  type CardPatch,
  type Column,
  answerQuestion,
  dropCard,
  moveCard,
  patchCard,
  readCard,
} from "./boardApi";

export interface CardDetailHandle {
  card: CardDetail | null;
  loading: boolean;
  /** Why the last read or change failed; the card shown stays the last good one. */
  error: string | null;
  busy: boolean;
  /** Save fields of the card; a card changed meanwhile is shown as it is now, with a notice. */
  save: (patch: Omit<CardPatch, "expected_updated">) => Promise<boolean>;
  answer: (index: number, text: string) => Promise<boolean>;
  move: (column: Column) => Promise<boolean>;
  drop: () => Promise<boolean>;
}

/**
 * One card with its text, read again whenever the board's head moves (`head`), so a poll that finds a change also
 * refreshes the open card. Every change tells the board through `onChanged`.
 */
export function useCardDetail(
  projectId: number,
  cardId: number,
  head: string | null,
  onChanged: () => Promise<void> | void,
): CardDetailHandle {
  const [card, setCard] = useState<CardDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setCard(null);
    setLoading(true);
    setError(null);
  }, [projectId, cardId]);

  useEffect(() => {
    let current = true;
    void readCard(projectId, cardId).then((answer) => {
      if (!current) return;
      setLoading(false);
      if (answer.ok) {
        setCard(answer.data);
        setError(null);
      } else {
        setError(answer.problem.message);
      }
    });
    return () => {
      current = false;
    };
  }, [projectId, cardId, head]);

  const run = useCallback(
    async (change: () => Promise<BoardResult<CardDetail | null>>) => {
      setBusy(true);
      setError(null);
      const answer = await change();
      setBusy(false);
      if (!answer.ok) {
        if (answer.problem.current) {
          setCard(answer.problem.current);
          setError(`${answer.problem.message} This is the card as it is now.`);
        } else {
          setError(answer.problem.message);
        }
        return false;
      }
      if (answer.data) setCard(answer.data);
      await onChanged();
      return true;
    },
    [onChanged],
  );

  const save = useCallback(
    (patch: Omit<CardPatch, "expected_updated">) =>
      run(() => patchCard(projectId, cardId, { ...patch, expected_updated: card?.updated ?? null })),
    [run, projectId, cardId, card?.updated],
  );
  const answer = useCallback(
    (index: number, text: string) => run(() => answerQuestion(projectId, cardId, index, text)),
    [run, projectId, cardId],
  );
  const move = useCallback(
    (column: Column) => run(() => moveCard(projectId, cardId, column, card?.updated ?? null)),
    [run, projectId, cardId, card?.updated],
  );
  const drop = useCallback(() => run(() => dropCard(projectId, cardId)), [run, projectId, cardId]);

  return { card, loading, error, busy, save, answer, move, drop };
}
