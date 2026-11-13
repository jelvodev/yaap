import { useCallback, useEffect, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";

export type PlanOut = components["schemas"]["PlanOut"];
export type PlanEdit = components["schemas"]["PlanEditIn"];

export interface PlanState {
  plan: PlanOut | null;
  /** Loading or a change failed: what to show. */
  error: string | null;
  /** The reasons an edit was not accepted, each naming its step. */
  invalid: string[];
  busy: boolean;
  save: (edit: PlanEdit) => Promise<boolean>;
  discard: () => Promise<void>;
  approve: () => Promise<void>;
  continueBuild: () => Promise<void>;
  stop: () => Promise<void>;
  answer: (approvalId: string, decision: "approve" | "deny") => Promise<void>;
}

const POLL_MS = 5000;

interface InvalidPlanDetail {
  code: "invalid_plan";
  errors: string[];
}

function invalidPlanErrors(error: unknown): string[] | null {
  if (!error || typeof error !== "object" || !("detail" in error)) return null;
  const detail: unknown = error.detail;
  if (!detail || typeof detail !== "object" || !("code" in detail) || !("errors" in detail)) return null;
  const candidate = detail as Partial<InvalidPlanDetail>;
  if (candidate.code !== "invalid_plan" || !Array.isArray(candidate.errors)) return null;
  return candidate.errors.filter((item): item is string => typeof item === "string");
}

/** The message of a refused build action: the 409 detail is `{code, message}`. */
export function refusalMessage(error: unknown, fallback: string): string {
  if (error && typeof error === "object" && "detail" in error) {
    const detail: unknown = error.detail;
    if (detail && typeof detail === "object" && "message" in detail && typeof detail.message === "string") {
      return detail.message;
    }
  }
  return errorMessage(error, fallback);
}

/** A plan the agent handed over: read it, edit it while it is a draft, discard it, or build it and follow the build. */
export function usePlan(planId: number): PlanState {
  const [plan, setPlan] = useState<PlanOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [invalid, setInvalid] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let current = true;
    const request = api.GET("/api/v1/plans/{plan_id}", { params: { path: { plan_id: planId } } });
    void request.then(({ data, error: failed }) => {
      if (!current) return;
      if (data) setPlan(data);
      else setError(errorMessage(failed, "The plan could not be loaded."));
    });
    return () => {
      current = false;
    };
  }, [planId]);

  const save = useCallback(
    async (edit: PlanEdit): Promise<boolean> => {
      setBusy(true);
      const { data, error: failed } = await api.PATCH("/api/v1/plans/{plan_id}", {
        params: { path: { plan_id: planId } },
        body: edit,
      });
      setBusy(false);
      if (data) {
        setPlan(data);
        setInvalid([]);
        setError(null);
        return true;
      }
      const errors = invalidPlanErrors(failed);
      if (errors) setInvalid(errors);
      else setError(errorMessage(failed, "The plan could not be saved."));
      return false;
    },
    [planId],
  );

  const discard = useCallback(async (): Promise<void> => {
    setBusy(true);
    const { data, error: failed } = await api.POST("/api/v1/plans/{plan_id}/discard", {
      params: { path: { plan_id: planId } },
    });
    setBusy(false);
    if (data) setPlan(data);
    else setError(errorMessage(failed, "The plan could not be discarded."));
  }, [planId]);

  const status = plan?.status;
  useEffect(() => {
    if (status !== "building") return;
    let current = true;
    const timer = setInterval(() => {
      void api.GET("/api/v1/plans/{plan_id}", { params: { path: { plan_id: planId } } }).then(({ data }) => {
        if (current && data) setPlan(data);
      });
    }, POLL_MS);
    return () => {
      current = false;
      clearInterval(timer);
    };
  }, [planId, status]);

  const build = useCallback(
    async (action: "approve" | "continue" | "stop", fallback: string): Promise<void> => {
      setBusy(true);
      const { data, error: failed } = await api.POST(`/api/v1/plans/{plan_id}/${action}`, {
        params: { path: { plan_id: planId } },
      });
      setBusy(false);
      if (data) {
        setPlan(data);
        setInvalid([]);
        setError(null);
        return;
      }
      const errors = invalidPlanErrors(failed);
      if (errors) setInvalid(errors);
      else setError(refusalMessage(failed, fallback));
    },
    [planId],
  );

  const approve = useCallback(() => build("approve", "The plan could not be approved."), [build]);
  const continueBuild = useCallback(() => build("continue", "The build could not be continued."), [build]);
  const stop = useCallback(() => build("stop", "The build could not be stopped."), [build]);

  const answer = useCallback(async (approvalId: string, decision: "approve" | "deny"): Promise<void> => {
    setBusy(true);
    const { error: failed } = await api.POST("/api/v1/approvals/{approval_id}", {
      params: { path: { approval_id: approvalId } },
      body: { decision, reason: null },
    });
    setBusy(false);
    if (failed) setError(errorMessage(failed, "The decision could not be sent."));
    else setError(null);
  }, []);

  return { plan, error, invalid, busy, save, discard, approve, continueBuild, stop, answer };
}
