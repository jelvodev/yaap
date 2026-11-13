import { useCallback, useEffect, useRef, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";
import type { PlanOut } from "../plan/usePlan";
import { refusalMessage } from "../plan/usePlan";
import type { CardActionName } from "./cardLogic";

export type CardDetail = components["schemas"]["CardDetailOut"];
export type CardActionResult = components["schemas"]["CardActionOut"];
export type ReviewDetail = components["schemas"]["ReviewOut"];
/**
 * What to send a card back with: a finding of its review (by index) or an unmet criterion (by number); nothing for a
 * card whose build failed or was interrupted.
 */
export type SendBackChoice = { finding: number } | { criterion: number } | Record<string, never>;

export interface CardState {
  card: CardDetail | null;
  plan: PlanOut | null;
  /** The card names a plan that does not exist any more. */
  planMissing: boolean;
  review: ReviewDetail | null;
  error: string | null;
  busy: boolean;
  /** Run a step on the card; resolves to the result, or null when it was refused (the error says why). */
  act: (action: CardActionName) => Promise<CardActionResult | null>;
  /** Answer an approval the build waits for. */
  answer: (approvalId: string, decision: "approve" | "deny") => Promise<void>;
  /** Send the card back to Design with one finding or unmet criterion in its description. */
  sendBack: (choice: SendBackChoice) => Promise<void>;
}

const POLL_MS = 5000;

interface Parts {
  plan: PlanOut | null;
  planMissing: boolean;
  review: ReviewDetail | null;
}

async function loadParts(projectId: number, card: CardDetail): Promise<Parts> {
  let plan: PlanOut | null = null;
  let planMissing = false;
  if (card.plan) {
    const { data, response } = await api.GET("/api/v1/plans/{plan_id}", {
      params: { path: { plan_id: card.plan.id } },
    });
    plan = data ?? null;
    planMissing = !data && response.status === 404;
  }
  let review: ReviewDetail | null = null;
  if (card.review_id !== null) {
    const { data } = await api.GET("/api/v1/projects/{project_id}/reviews/{review_id}", {
      params: { path: { project_id: projectId, review_id: card.review_id } },
    });
    review = data ?? null;
  }
  return { plan, planMissing, review };
}

/** A card of the project board: loaded, followed while something runs on it, and acted on. */
export function useCard(projectId: number, cardId: number): CardState {
  const [card, setCard] = useState<CardDetail | null>(null);
  const [parts, setParts] = useState<Parts>({ plan: null, planMissing: false, review: null });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const apply = useCallback(
    async (next: CardDetail): Promise<void> => {
      const loaded = await loadParts(projectId, next);
      if (!mounted.current) return;
      setCard(next);
      setParts(loaded);
    },
    [projectId],
  );

  const refresh = useCallback(async (): Promise<void> => {
    const { data, error: failed } = await api.GET("/api/v1/projects/{project_id}/board/cards/{card_id}", {
      params: { path: { project_id: projectId, card_id: cardId } },
    });
    if (!mounted.current) return;
    if (!data) {
      setError(errorMessage(failed, "The card could not be loaded."));
      return;
    }
    setError(null);
    await apply(data);
  }, [projectId, cardId, apply]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const following = card?.state === "working" || card?.review === "running" || parts.plan?.status === "building" ||
    parts.plan?.status === "queued";
  useEffect(() => {
    if (!following) return;
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [following, refresh]);

  const act = useCallback(
    async (action: CardActionName): Promise<CardActionResult | null> => {
      setBusy(true);
      const { data, error: failed } = await api.POST(
        "/api/v1/projects/{project_id}/board/cards/{card_id}/actions/{action}",
        { params: { path: { project_id: projectId, card_id: cardId, action } } },
      );
      if (data) {
        setError(null);
        await apply(data.card);
      } else if (mounted.current) {
        setError(refusalMessage(failed, "That step could not be started."));
      }
      if (mounted.current) setBusy(false);
      return data ?? null;
    },
    [projectId, cardId, apply],
  );

  const answer = useCallback(
    async (approvalId: string, decision: "approve" | "deny"): Promise<void> => {
      setBusy(true);
      const { error: failed } = await api.POST("/api/v1/approvals/{approval_id}", {
        params: { path: { approval_id: approvalId } },
        body: { decision, reason: null },
      });
      if (!mounted.current) return;
      setBusy(false);
      if (failed) setError(errorMessage(failed, "The decision could not be sent."));
      else void refresh();
    },
    [refresh],
  );

  const sendBack = useCallback(
    async (choice: SendBackChoice): Promise<void> => {
      setBusy(true);
      const { data, error: failed } = await api.POST("/api/v1/projects/{project_id}/board/cards/{card_id}/send-back", {
        params: { path: { project_id: projectId, card_id: cardId } },
        body: choice,
      });
      if (data) {
        setError(null);
        await apply(data);
      } else if (mounted.current) {
        setError(errorMessage(failed, "The card could not be sent back."));
      }
      if (mounted.current) setBusy(false);
    },
    [projectId, cardId, apply],
  );

  return { card, ...parts, error, busy, act, answer, sendBack };
}
