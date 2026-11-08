import { useCallback, useEffect, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";

export type EvalCase = components["schemas"]["EvalCaseOut"];
export type EvalRun = components["schemas"]["EvalRunOut"];
export type EvalRunDetail = components["schemas"]["EvalRunDetailOut"];
export type EvalComparison = components["schemas"]["ComparisonOut"];
export type EvalVariant = components["schemas"]["VariantIn"];

const POLL_MS = 3000;

export function isOpen(run: EvalRun): boolean {
  return run.status === "queued" || run.status === "running";
}

export interface Evals {
  cases: EvalCase[] | null;
  runs: EvalRun[] | null;
  /** The run shown with its plays, kept up to date while it runs. */
  detail: EvalRunDetail | null;
  select: (runId: number | null) => void;
  error: string | null;
  start: (caseName: string | null, repeats: number) => Promise<void>;
  startComparison: (variant: EvalVariant, caseName: string | null, repeats: number) => Promise<EvalRun[] | null>;
  stop: (runId: number) => Promise<void>;
  remove: (runId: number) => Promise<void>;
  reload: () => Promise<void>;
}

/** The Evals tab's data: the project's cases, its runs and the selected run, read again every few
 * seconds while a run goes on. */
export function useEvals(projectId: number): Evals {
  const [cases, setCases] = useState<EvalCase[] | null>(null);
  const [runs, setRuns] = useState<EvalRun[] | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [detail, setDetail] = useState<EvalRunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const path = { params: { path: { project_id: projectId } } };

  const loadRuns = useCallback(async () => {
    const { data } = await api.GET("/api/v1/projects/{project_id}/evals/runs", path);
    setRuns(data ?? []);
  }, [projectId]);

  const loadDetail = useCallback(async () => {
    if (selected === null) {
      setDetail(null);
      return;
    }
    const { data } = await api.GET("/api/v1/projects/{project_id}/evals/runs/{run_id}", {
      params: { path: { project_id: projectId, run_id: selected } },
    });
    setDetail(data ?? null);
  }, [projectId, selected]);

  const reload = useCallback(async () => {
    const { data } = await api.GET("/api/v1/projects/{project_id}/evals/cases", path);
    setCases(data ?? []);
    await loadRuns();
  }, [projectId, loadRuns]);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    void loadDetail();
  }, [loadDetail]);

  const anyOpen = (runs ?? []).some(isOpen);
  useEffect(() => {
    if (!anyOpen) return;
    const timer = window.setInterval(() => {
      void loadRuns();
      void loadDetail();
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [anyOpen, loadRuns, loadDetail]);

  async function start(caseName: string | null, repeats: number) {
    setError(null);
    const { data, error: failure } = await api.POST("/api/v1/projects/{project_id}/evals/runs", {
      ...path,
      body: { case_name: caseName, repeats },
    });
    if (failure || !data) {
      setError(errorMessage(failure, "The run could not start."));
      return;
    }
    setSelected(data.id);
    await loadRuns();
  }

  async function startComparison(variant: EvalVariant, caseName: string | null, repeats: number) {
    setError(null);
    const { data, error: failure } = await api.POST("/api/v1/projects/{project_id}/evals/comparisons", {
      ...path,
      body: { variant, case_name: caseName, repeats },
    });
    if (failure || !data) {
      setError(errorMessage(failure, "The comparison could not start."));
      return null;
    }
    await loadRuns();
    return data;
  }

  async function stop(runId: number) {
    await api.POST("/api/v1/projects/{project_id}/evals/runs/{run_id}/stop", {
      params: { path: { project_id: projectId, run_id: runId } },
    });
    await loadRuns();
    await loadDetail();
  }

  async function remove(runId: number) {
    setError(null);
    const { error: failure } = await api.DELETE("/api/v1/projects/{project_id}/evals/runs/{run_id}", {
      params: { path: { project_id: projectId, run_id: runId } },
    });
    if (failure) {
      setError(errorMessage(failure, "The run could not be removed."));
      return;
    }
    if (selected === runId) setSelected(null);
    await loadRuns();
  }

  return { cases, runs, detail, select: setSelected, error, start, startComparison, stop, remove, reload };
}

/** Two runs side by side, read again while either of them runs. */
export function useEvalComparison(projectId: number, a: number | null, b: number | null, runs: EvalRun[] | null) {
  const [comparison, setComparison] = useState<EvalComparison | null>(null);
  const stamp = (runs ?? [])
    .filter((run) => run.id === a || run.id === b)
    .map((run) => `${run.id}:${run.finished}:${run.status}`)
    .join("|");

  useEffect(() => {
    if (a === null || b === null || a === b) {
      setComparison(null);
      return;
    }
    let current = true;
    void api
      .GET("/api/v1/projects/{project_id}/evals/compare", { params: { path: { project_id: projectId }, query: { a, b } } })
      .then(({ data }) => current && setComparison(data ?? null));
    return () => {
      current = false;
    };
  }, [projectId, a, b, stamp]);

  return comparison;
}
