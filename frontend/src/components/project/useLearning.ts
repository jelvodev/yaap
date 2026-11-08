import { useCallback, useEffect, useRef, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";

export type LearningProposal = components["schemas"]["LearningProposalOut"];
export type LearningRule = components["schemas"]["RuleIO"];
export type ContradictionReport = components["schemas"]["ContradictionReportOut"];

const POLL_MS = 3000;

export interface Learning {
  proposals: LearningProposal[] | null;
  rules: LearningRule[];
  report: ContradictionReport | null;
  busy: string | null;
  error: string | null;
  notice: string | null;
  measure: (proposal: LearningProposal) => Promise<void>;
  accept: (proposal: LearningProposal) => Promise<void>;
  decline: (proposal: LearningProposal) => Promise<void>;
  checkRule: (rule: LearningRule) => Promise<void>;
  findContradictions: () => Promise<void>;
  reload: () => Promise<void>;
}

/** The open learning proposals of a project, shared by the Learning tab and the count on the rail. */
export interface LearningProposals {
  /** The open proposals, or null while they are not read. */
  proposals: LearningProposal[] | null;
  reload: () => Promise<void>;
}

/**
 * The open proposals, read while `enabled` and again every few seconds while one is being measured.
 * When it is no longer enabled the list is forgotten, so the Learning tab starts from "Loading…" as before.
 */
export function useLearningProposals(projectId: number | null, enabled: boolean): LearningProposals {
  const [proposals, setProposals] = useState<LearningProposal[] | null>(null);
  // The project the latest read is for; an answer for another one arrives too late and is dropped.
  const wanted = useRef(projectId);
  wanted.current = projectId;

  const reload = useCallback(async () => {
    if (projectId === null) return;
    const { data } = await api.GET("/api/v1/projects/{project_id}/learning/proposals", {
      params: { path: { project_id: projectId } },
    });
    if (wanted.current === projectId) setProposals(data ?? []);
  }, [projectId]);

  useEffect(() => {
    setProposals(null);
    if (enabled) void reload();
  }, [enabled, reload]);

  const measuring = (proposals ?? []).some((proposal) => proposal.measurement.status === "measuring");
  useEffect(() => {
    if (!enabled || !measuring) return;
    const timer = window.setInterval(() => void reload(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [enabled, measuring, reload]);

  return { proposals, reload };
}

/** The Learning tab's data: the open proposals (shared, see `useLearningProposals`) and the checks
 * of the project's rules. */
export function useLearning(projectId: number, shared: LearningProposals): Learning {
  const [rules, setRules] = useState<LearningRule[]>([]);
  const [report, setReport] = useState<ContradictionReport | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const path = { project_id: projectId };
  const reloadProposals = shared.reload;

  const loadRules = useCallback(async () => {
    const { data } = await api.GET("/api/v1/projects/{project_id}/learning/rules", { params: { path } });
    setRules(data ?? []);
  }, [projectId]);

  const reload = useCallback(async () => {
    await reloadProposals();
    await loadRules();
  }, [reloadProposals, loadRules]);

  // The proposals are read by the shared hook as the tab opens; the rules here.
  useEffect(() => {
    void loadRules();
  }, [loadRules]);

  async function act(name: string, request: () => Promise<{ error?: unknown }>, done: string | null) {
    setBusy(name);
    setError(null);
    setNotice(null);
    const { error: failure } = await request();
    setBusy(null);
    if (failure) setError(errorMessage(failure));
    else if (done) setNotice(done);
    await reload();
  }

  const proposalPath = (proposal: LearningProposal) => ({ params: { path: { ...path, proposal_id: proposal.id } } });

  return {
    proposals: shared.proposals,
    rules,
    report,
    busy,
    error,
    notice,
    reload,
    measure: (proposal) =>
      act(
        `measure-${proposal.id}`,
        () => api.POST("/api/v1/projects/{project_id}/learning/proposals/{proposal_id}/measure", proposalPath(proposal)),
        null,
      ),
    accept: (proposal) =>
      act(
        `accept-${proposal.id}`,
        () => api.POST("/api/v1/projects/{project_id}/learning/proposals/{proposal_id}/accept", proposalPath(proposal)),
        "Accepted: written as one commit.",
      ),
    decline: (proposal) =>
      act(
        `decline-${proposal.id}`,
        () => api.POST("/api/v1/projects/{project_id}/learning/proposals/{proposal_id}/decline", proposalPath(proposal)),
        "Declined: nothing was changed.",
      ),
    checkRule: (rule) =>
      act("check", () => api.POST("/api/v1/projects/{project_id}/learning/rule-checks", { params: { path }, body: rule }), null),
    findContradictions: async () => {
      setBusy("contradictions");
      setError(null);
      const { data, error: failure } = await api.POST("/api/v1/projects/{project_id}/learning/contradictions", {
        params: { path },
      });
      setBusy(null);
      if (failure || !data) setError(errorMessage(failure, "The rules could not be checked."));
      setReport(data ?? null);
    },
  };
}
