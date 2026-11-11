import { useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { components } from "../api/schema";
import DiffView from "./DiffView";

type Level = components["schemas"]["RuleProposalOut"]["level"];

/** Where a conversation's rules can go: its environment and, in a project, its project. */
export interface RuleTarget {
  conversationId: number;
  environmentId: number;
  environmentName: string;
  projectId: number | null;
  projectName: string | null;
}

/**
 * Remember as rule: the utility model drafts a rule from the user's correction, the user edits it,
 * picks the level, previews the change to the Blueprint as a diff and saves it.
 */
export default function RulePanel({
  messageId,
  target,
  onClose,
}: {
  messageId: number;
  target: RuleTarget;
  onClose: () => void;
}) {
  const [rule, setRule] = useState("");
  const [level, setLevel] = useState<Level>(target.projectId ? "project" : "environment");
  const [path, setPath] = useState<string | null>(null);
  const [drafting, setDrafting] = useState(true);
  // Why no model drafted the rule, when the model policy skipped the task.
  const [skipped, setSkipped] = useState<string | null>(null);
  const [preview, setPreview] = useState<components["schemas"]["RuleOut"] | null>(null);
  const [saved, setSaved] = useState<components["schemas"]["RuleOut"] | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    void api
      .POST("/api/v1/conversations/{conversation_id}/messages/{message_id}/rule-proposal", {
        params: { path: { conversation_id: target.conversationId, message_id: messageId } },
      })
      .then(({ data, error: requestError }) => {
        if (!current) return;
        setDrafting(false);
        if (requestError || !data) {
          setError(errorMessage(requestError, "No rule could be drafted from this message."));
          return;
        }
        setRule(data.rule);
        setLevel(data.level);
        setPath(data.path);
        setSkipped(data.skipped ?? null);
      });
    return () => {
      current = false;
    };
  }, [messageId, target.conversationId]);

  function levelQuery(): { environment_id?: number; project_id?: number } {
    if (level === "global") return {};
    if (level === "environment") return { environment_id: target.environmentId };
    return { environment_id: target.environmentId, project_id: target.projectId ?? undefined };
  }

  async function submit(apply: boolean) {
    setWorking(true);
    setError(null);
    const { data, error: requestError } = await api.POST("/api/v1/blueprint/rule", {
      params: { query: levelQuery() },
      body: { rule, path, apply },
    });
    setWorking(false);
    if (requestError || !data) {
      setError(errorMessage(requestError, "The rule could not be added."));
      return;
    }
    if (apply) setSaved(data);
    else setPreview(data);
  }

  const levelLabel =
    level === "global" ? "Global" : level === "environment" ? target.environmentName : (target.projectName ?? "Project");

  if (saved) {
    return (
      <div className="rule-panel" aria-label="Remember as rule">
        <p>
          Saved in <code>{saved.path}</code> ({levelLabel}). It applies from the next reply on.
        </p>
        {saved.mutation && !saved.mutation.committed && (
          <p className="form-error">
            Saved, but not committed: {saved.mutation.error ?? saved.mutation.path}. Apply the rule again to retry.
          </p>
        )}
        <div className="form-row">
          <button type="button" className="button button-ghost" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    );
  }

  return (
    <form
      className="rule-panel"
      aria-label="Remember as rule"
      onSubmit={(event) => {
        event.preventDefault();
        void submit(preview !== null);
      }}
    >
      <label htmlFor={`rule-${messageId}`}>Remember as rule</label>
      {drafting ? (
        <p className="faint small">Drafting a rule from your message…</p>
      ) : (
        <>
          {skipped && <p className="faint small">{skipped}</p>}
          <textarea
            id={`rule-${messageId}`}
            value={rule}
            rows={2}
            maxLength={500}
            onChange={(event) => {
              setRule(event.target.value);
              setPreview(null);
            }}
          />
          <div className="rule-options">
            <label>
              Level{" "}
              <select
                value={level}
                onChange={(event) => {
                  setLevel(event.target.value as Level);
                  setPreview(null);
                }}
              >
                <option value="global">Global</option>
                <option value="environment">Environment: {target.environmentName}</option>
                {target.projectId !== null && <option value="project">Project: {target.projectName}</option>}
              </select>
            </label>
            {path && <span className="faint small">Proposed for {path}, when this level has it.</span>}
          </div>
          {preview && (
            <>
              <p className="faint small">
                Change to <code>{preview.path}</code>:
              </p>
              <DiffView diff={preview.diff} />
            </>
          )}
        </>
      )}
      {error && <p className="form-error">{error}</p>}
      <div className="form-row">
        <button type="submit" className="button button-primary" disabled={drafting || working || !rule.trim()}>
          {preview ? "Save rule" : "Preview change"}
        </button>
        <button type="button" className="button button-ghost" onClick={onClose}>
          Cancel
        </button>
      </div>
    </form>
  );
}
