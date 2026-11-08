import { useEffect, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";

export type EvalCaseDraft = components["schemas"]["EvalCaseIO"];
export type EvalCheck = EvalCaseDraft["checks"][number];
export type EvalCheckKind = EvalCheck["kind"];
export type EvalCaseProposal = components["schemas"]["EvalCaseProposalOut"];
export type EvalCaseSaved = components["schemas"]["EvalCaseSavedOut"];
export type StartKind = "commit" | "checkpoint";

export interface EvalCaseDraftState {
  proposal: EvalCaseProposal | null;
  draft: EvalCaseDraft | null;
  startKind: StartKind;
  loading: boolean;
  saving: boolean;
  saved: EvalCaseSaved | null;
  error: string | null;
  change: (patch: Partial<EvalCaseDraft>) => void;
  setStartKind: (kind: StartKind) => void;
  changeCheck: (index: number, check: EvalCheck) => void;
  addCheck: (kind: EvalCheckKind) => void;
  removeCheck: (index: number) => void;
  save: () => Promise<void>;
}

export function emptyCheck(kind: EvalCheckKind): EvalCheck {
  if (kind === "command") return { kind, name: "", run: "", timeout_seconds: 600 };
  if (kind === "file") return { kind, name: "", path: "", exists: true, contains: [], not_contains: [], matches: [], not_matches: [] };
  return { kind, name: "", rubric: "", files: [] };
}

/** The list fields are edited one text per line; empty lines are dropped before saving. */
function filled(values: string[] | undefined): string[] {
  return (values ?? []).filter((value) => value.trim().length > 0);
}

export function cleanCheck(check: EvalCheck): EvalCheck {
  if (check.kind === "command") return check;
  if (check.kind === "rubric") return { ...check, files: filled(check.files) };
  return {
    ...check,
    contains: filled(check.contains),
    not_contains: filled(check.not_contains),
    matches: filled(check.matches),
    not_matches: filled(check.not_matches),
  };
}

/**
 * A case from a conversation: the proposal the backend makes from the conversation, the user's edits to
 * it, and saving it as `.blueprint/evals/<name>.md` in the project, one commit.
 */
export function useEvalCaseDraft(conversationId: number, projectId: number): EvalCaseDraftState {
  const [proposal, setProposal] = useState<EvalCaseProposal | null>(null);
  const [draft, setDraft] = useState<EvalCaseDraft | null>(null);
  const [startKind, setStartKind] = useState<StartKind>("commit");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<EvalCaseSaved | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    setLoading(true);
    void api
      .POST("/api/v1/conversations/{conversation_id}/eval-case-proposal", {
        params: { path: { conversation_id: conversationId } },
      })
      .then(({ data, error: requestError }) => {
        if (!current) return;
        setLoading(false);
        if (!data) {
          setError(errorMessage(requestError, "No case could be proposed from this conversation."));
          return;
        }
        setProposal(data);
        setDraft(data.case);
        setStartKind(data.case.start.checkpoint != null ? "checkpoint" : "commit");
      });
    return () => {
      current = false;
    };
  }, [conversationId]);

  function change(patch: Partial<EvalCaseDraft>) {
    setDraft((previous) => (previous ? { ...previous, ...patch } : previous));
  }

  function changeChecks(update: (checks: EvalCheck[]) => EvalCheck[]) {
    setDraft((previous) => (previous ? { ...previous, checks: update(previous.checks) } : previous));
  }

  async function save() {
    if (!draft) return;
    setSaving(true);
    setError(null);
    const start =
      startKind === "commit" ? { commit: draft.start.commit ?? null } : { checkpoint: draft.start.checkpoint ?? null };
    const { data, error: requestError } = await api.POST("/api/v1/projects/{project_id}/eval-cases", {
      params: { path: { project_id: projectId } },
      body: { ...draft, start, checks: draft.checks.map(cleanCheck) },
    });
    setSaving(false);
    if (!data) setError(errorMessage(requestError, "The case could not be saved."));
    else setSaved(data);
  }

  return {
    proposal,
    draft,
    startKind,
    loading,
    saving,
    saved,
    error,
    change,
    setStartKind,
    changeCheck: (index, check) => changeChecks((checks) => checks.map((item, at) => (at === index ? check : item))),
    addCheck: (kind) => changeChecks((checks) => [...checks, emptyCheck(kind)]),
    removeCheck: (index) => changeChecks((checks) => checks.filter((_item, at) => at !== index)),
    save,
  };
}
