import { useCallback, useEffect, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";

export type ReviewSummary = components["schemas"]["ReviewSummaryOut"];
export type Review = components["schemas"]["ReviewOut"];
export type Finding = components["schemas"]["FindingOut"];

const POLL_MS = 2500;
const LATE_RELOAD_MS = 4000;

export const REVIEW_LABELS: Record<ReviewSummary["status"], string> = {
  running: "Reviewing",
  done: "Reviewed",
  skipped: "Skipped",
  failed: "Failed",
};

export const SOURCE_LABELS: Record<ReviewSummary["source"], string> = {
  goal: "after a goal",
  task: "after a scheduled task",
  changes: "uncommitted changes",
  conversation: "a conversation's changes",
  reply: "a reply's changes",
  answer: "a reply's answer",
};

/** What a review found in a few words, for a list row or a chip. */
export function reviewOutcome(review: ReviewSummary): string {
  if (review.status !== "done") return REVIEW_LABELS[review.status];
  if (review.findings_count === 0) return "No findings";
  const high = review.high_count ? ` (${review.high_count} high)` : "";
  return `${review.findings_count} finding${review.findings_count === 1 ? "" : "s"}${high}`;
}

/** Start a review of the project's uncommitted changes (no conversation) or of a conversation's changes. */
export async function startReview(projectId: number, conversationId: number | null): Promise<{ review?: Review; error?: string }> {
  const { data, error } = await api.POST("/api/v1/projects/{project_id}/reviews", {
    params: { path: { project_id: projectId } },
    body: { conversation_id: conversationId },
  });
  return data ? { review: data } : { error: errorMessage(error, "The review could not start.") };
}

export interface Reviews {
  reviews: ReviewSummary[] | null;
  /** The review shown with its report and diff, kept up to date while it runs. */
  detail: Review | null;
  select: (reviewId: number | null) => void;
  reload: () => Promise<void>;
}

/** A project's reviews, optionally of one conversation, read again while one runs and when version
 * changes (a reply ended, after which a goal's review may start). */
export function useReviews(
  projectId: number,
  conversationId: number | null = null,
  focus: number | null = null,
  version: string | null = null,
): Reviews {
  const [reviews, setReviews] = useState<ReviewSummary[] | null>(null);
  const [selected, setSelected] = useState<number | null>(focus);
  const [detail, setDetail] = useState<Review | null>(null);

  useEffect(() => {
    if (focus !== null) setSelected(focus);
  }, [focus]);

  const reload = useCallback(async () => {
    const { data } = await api.GET("/api/v1/projects/{project_id}/reviews", {
      params: { path: { project_id: projectId }, query: conversationId === null ? {} : { conversation_id: conversationId } },
    });
    setReviews(data ?? []);
  }, [projectId, conversationId]);

  const loadDetail = useCallback(async () => {
    if (selected === null) {
      setDetail(null);
      return;
    }
    const { data } = await api.GET("/api/v1/projects/{project_id}/reviews/{review_id}", {
      params: { path: { project_id: projectId, review_id: selected } },
    });
    setDetail(data ?? null);
  }, [projectId, selected]);

  useEffect(() => {
    void reload();
    if (version === null) return;
    // A goal's review starts a moment after its last reply ends.
    const later = window.setTimeout(() => void reload(), LATE_RELOAD_MS);
    return () => window.clearTimeout(later);
  }, [reload, version]);

  useEffect(() => {
    void loadDetail();
  }, [loadDetail]);

  const anyRunning = (reviews ?? []).some((review) => review.status === "running");
  useEffect(() => {
    if (!anyRunning) return;
    const timer = window.setInterval(() => {
      void reload();
      void loadDetail();
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [anyRunning, reload, loadDetail]);

  return { reviews, detail, select: setSelected, reload };
}
