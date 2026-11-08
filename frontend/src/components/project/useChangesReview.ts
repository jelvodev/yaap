import { useCallback, useEffect, useState } from "react";

import { api } from "../../api/client";
import { type Review, startReview } from "./useReviews";

const POLL_MS = 2500;

export interface ChangesReview {
  /** The review started from the Changes panel, kept up to date while it runs; null before one starts. */
  review: Review | null;
  starting: boolean;
  /** Why the review could not start. */
  error: string | null;
  start: () => Promise<void>;
  dismiss: () => void;
}

/** A review of the project's uncommitted changes by another vendor, started and followed here. */
export function useChangesReview(projectId: number): ChangesReview {
  const [review, setReview] = useState<Review | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setReview(null);
    setError(null);
  }, [projectId]);

  const start = useCallback(async () => {
    setStarting(true);
    setError(null);
    const started = await startReview(projectId, null);
    setStarting(false);
    setReview(started.review ?? null);
    if (!started.review) setError(started.error ?? "The review could not start.");
  }, [projectId]);

  const reviewId = review?.id ?? null;
  const running = review?.status === "running";
  useEffect(() => {
    if (reviewId === null || !running) return;
    const timer = window.setInterval(() => {
      void api
        .GET("/api/v1/projects/{project_id}/reviews/{review_id}", {
          params: { path: { project_id: projectId, review_id: reviewId } },
        })
        .then(({ data }) => {
          if (data) setReview(data);
        });
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [projectId, reviewId, running]);

  const dismiss = useCallback(() => {
    setReview(null);
    setError(null);
  }, []);

  return { review, starting, error, start, dismiss };
}
