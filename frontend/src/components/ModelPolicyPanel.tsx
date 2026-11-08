import { useEffect, useState } from "react";

import { type PolicyLevel, type PolicyValue, useModelPolicy } from "./modelPolicy";

interface Choice {
  key: string;
  label: string;
  detail?: string;
  /** Why it cannot be chosen here (the environment does not allow it), or undefined. */
  blocked?: string;
}

/** Any, or only the checked items; models also have "only local models". */
function ChoiceList({
  id,
  title,
  description,
  anyLabel,
  choices,
  value,
  localOption,
  onChange,
}: {
  id: string;
  title: string;
  description: string;
  anyLabel: string;
  choices: Choice[];
  value: string[] | "local" | undefined;
  localOption?: boolean;
  onChange: (value: string[] | "local" | undefined) => void;
}) {
  const mode = value === undefined ? "any" : value === "local" ? "local" : "list";
  const checked = Array.isArray(value) ? value : [];

  function toggle(key: string, on: boolean) {
    onChange(on ? [...checked, key] : checked.filter((item) => item !== key));
  }

  return (
    <fieldset className="card policy-choices">
      <legend className="card-header">
        <h3>{title}</h3>
      </legend>
      <p className="muted small">{description}</p>
      <label className="policy-mode">
        <input type="radio" name={`${id}-mode`} checked={mode === "any"} onChange={() => onChange(undefined)} />{" "}
        {anyLabel}
      </label>
      {localOption && (
        <label className="policy-mode">
          <input type="radio" name={`${id}-mode`} checked={mode === "local"} onChange={() => onChange("local")} /> Only
          local models (Ollama)
        </label>
      )}
      <label className="policy-mode">
        <input type="radio" name={`${id}-mode`} checked={mode === "list"} onChange={() => onChange(checked)} /> Only
        these:
      </label>
      {mode === "list" && (
        <ul className="policy-list">
          {choices.length === 0 && <li className="faint small">Nothing to choose from yet.</li>}
          {choices.map((choice) => (
            <li key={choice.key}>
              <label title={choice.blocked}>
                <input
                  type="checkbox"
                  checked={checked.includes(choice.key)}
                  disabled={choice.blocked !== undefined && !checked.includes(choice.key)}
                  onChange={(event) => toggle(choice.key, event.target.checked)}
                />{" "}
                {choice.label}
                {choice.detail && <span className="faint small"> {choice.detail}</span>}
                {choice.blocked && <span className="faint small"> — {choice.blocked}</span>}
              </label>
            </li>
          ))}
        </ul>
      )}
    </fieldset>
  );
}

function describe(policy: PolicyValue): string {
  const parts: string[] = [];
  if (policy.connections) parts.push(`connections: ${policy.connections.join(", ") || "none"}`);
  if (policy.models === "local") parts.push("only local models");
  else if (policy.models) parts.push(`models: ${policy.models.join(", ") || "none"}`);
  if (policy.business_data) parts.push("models marked for business data processing");
  if (policy.agents) parts.push(`A2A agents: ${policy.agents.join(", ") || "none"}`);
  if (policy.patterns?.length) parts.push(`${policy.patterns.length} blocked pattern(s)`);
  return parts.length ? parts.join("; ") : "no limits";
}

/** Leave out the parts that are open, so a missing key keeps meaning "any" (or "inherited" in a project). */
function cleaned(draft: PolicyValue): PolicyValue {
  const value: PolicyValue = {};
  if (draft.connections) value.connections = draft.connections;
  if (draft.models) value.models = draft.models;
  if (draft.business_data !== undefined) value.business_data = draft.business_data;
  if (draft.agents) value.agents = draft.agents;
  const patterns = (draft.patterns ?? []).map((line) => line.trim()).filter(Boolean);
  if (patterns.length) value.patterns = patterns;
  return value;
}

/**
 * The model policy of an environment or a project, set like the tool permissions: which connections,
 * models and A2A agents may be used, and patterns that block a request before it leaves YAAP. A project narrows its
 * environment's policy; what the environment does not allow cannot be chosen.
 */
export default function ModelPolicyPanel({ level }: { level: PolicyLevel }) {
  const policy = useModelPolicy(level);
  const [draft, setDraft] = useState<PolicyValue>({});
  const [patternText, setPatternText] = useState("");
  const [message, setMessage] = useState<{ error: boolean; text: string } | null>(null);
  const inProject = level.kind === "project";
  const above = policy.inherited;

  useEffect(() => {
    setDraft(policy.own ?? {});
    setPatternText((policy.own?.patterns ?? []).join("\n"));
  }, [policy.own]);

  if (!policy.loaded) return <p className="faint small">Loading the policy…</p>;

  const outside = "the environment does not allow it";
  const localProviders = new Set(policy.connections.filter((item) => item.local).map((item) => item.provider));
  const connectionChoices: Choice[] = policy.connections.map((connection) => ({
    key: connection.key,
    label: connection.label,
    detail: connection.local ? `${connection.key}, local` : connection.key,
    blocked: above.connections && !above.connections.includes(connection.key) ? outside : undefined,
  }));
  const modelChoices: Choice[] = policy.models.map((model) => ({
    key: model.id,
    label: model.display_name,
    detail: `${model.provider_label}${model.business_data ? " · business data" : ""}`,
    blocked:
      Array.isArray(above.models) && !above.models.includes(model.id)
        ? outside
        : above.models === "local" && !localProviders.has(model.provider)
          ? "the environment allows only local models"
          : undefined,
  }));
  const agentChoices: Choice[] = [...new Set([...policy.agents, ...(draft.agents ?? [])])].sort().map((name) => ({
    key: name,
    label: name,
    detail: policy.agents.includes(name) ? undefined : "(not configured)",
    blocked: above.agents && !above.agents.includes(name) ? outside : undefined,
  }));

  async function save() {
    const problem = await policy.save(cleaned({ ...draft, patterns: patternText.split("\n") }));
    setMessage(problem ? { error: true, text: problem } : { error: false, text: "Saved." });
  }

  async function reset() {
    const problem = await policy.reset();
    setMessage(problem ? { error: true, text: problem } : { error: false, text: inProject ? "Back to the environment's policy." : "Removed." });
  }

  const anyLabel = inProject ? "As the environment allows" : "Any";
  return (
    <div className="wb-stack model-policy">
      {inProject && (
        <p className="muted small">
          The environment's policy: {describe(above)}. A project can only narrow it; both apply to every request here.
        </p>
      )}
      <ChoiceList
        id="policy-connections"
        title="Connections"
        description="Which ways in may be used: an API key, a subscription through Claude Code or Codex, or Ollama on your own computer."
        anyLabel={anyLabel}
        choices={connectionChoices}
        value={draft.connections}
        onChange={(value) => setDraft({ ...draft, connections: Array.isArray(value) ? value : undefined })}
      />
      <ChoiceList
        id="policy-models"
        title="Models"
        description="Which models may answer: the conversation, delegated tasks, Claude Code and Codex as workers, and the utility model."
        anyLabel={anyLabel}
        choices={modelChoices}
        value={draft.models}
        localOption
        onChange={(value) => setDraft({ ...draft, models: value })}
      />
      <div className="card">
        <label>
          <input
            type="checkbox"
            checked={draft.business_data === true || (inProject && above.business_data === true)}
            disabled={inProject && above.business_data === true}
            onChange={(event) => setDraft({ ...draft, business_data: event.target.checked ? true : undefined })}
          />{" "}
          Require models marked for business data processing
        </label>
        <p className="muted small">
          Only models whose vendor processes prompts under terms for business data, as the catalog marks them; local
          models always qualify.{inProject && above.business_data ? " Required by the environment." : ""}
        </p>
      </div>
      <ChoiceList
        id="policy-agents"
        title="A2A agents"
        description="Which remote agents call_agent may reach. An agent outside the list is refused before anything is asked or sent."
        anyLabel={inProject ? "As the environment allows" : "Every configured agent"}
        choices={agentChoices}
        value={draft.agents}
        onChange={(value) => setDraft({ ...draft, agents: Array.isArray(value) ? value : undefined })}
      />
      <div className="card">
        <div className="card-header">
          <h3>Blocked patterns</h3>
        </div>
        <p className="muted small">
          One regular expression per line. A model request, a tool's output for the model, or an A2A message that
          matches one is blocked before it leaves YAAP, naming the pattern; known secret values are always blocked.
          {inProject ? " These come on top of the environment's patterns." : ""}
        </p>
        <label className="visually-hidden" htmlFor="policy-patterns">
          Blocked patterns
        </label>
        <textarea
          id="policy-patterns"
          className="mono list-editor"
          rows={Math.min(10, Math.max(3, patternText.split("\n").length + 1))}
          value={patternText}
          placeholder={"\\bNL\\d{2}[A-Z]{4}\\d{10}\\b"}
          spellCheck={false}
          onChange={(event) => setPatternText(event.target.value)}
        />
      </div>
      <div className="form-row">
        <button type="button" className="button button-primary" onClick={() => void save()}>
          Save policy
        </button>
        {policy.own && (
          <button type="button" className="button button-ghost" onClick={() => void reset()}>
            {inProject ? "Back to the environment's policy" : "Remove the policy"}
          </button>
        )}
      </div>
      {message && <p className={message.error ? "form-error" : "muted small"}>{message.text}</p>}
    </div>
  );
}
