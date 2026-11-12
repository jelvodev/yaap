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
}

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

/** A plan the agent handed over: read it, edit it while it is a draft, or discard it. */
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

  return { plan, error, invalid, busy, save, discard };
}
