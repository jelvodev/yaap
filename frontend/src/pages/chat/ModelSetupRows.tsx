import { type ReactNode, useState } from "react";

import type { Conversation, ModelInfo, Preset } from "../../api/client";
import ContextBreakdown from "../../components/ContextBreakdown";
import ContextLimitSelect from "../../components/ContextLimitSelect";
import ModelOptions from "../../components/ModelOptions";
import { type ContextUse, formatTokens } from "../../components/composer/modelSetup";
import type { AgentInfo } from "./useCommands";
import type { DraftConversation } from "./useDraftConversation";

/** One row of the model setup's popover: a label on the left, its control on the right. */
function Row({ label, htmlFor, children }: { label: string; htmlFor?: string; children: ReactNode }) {
  return (
    <div className="model-setup-row">
      {htmlFor ? <label htmlFor={htmlFor}>{label}</label> : <span>{label}</span>}
      <span className="model-setup-control">{children}</span>
    </div>
  );
}

/** The agent a conversation works as; "" is plain YAAP. An agent that left the Blueprint stays shown as missing. */
function AgentRow({
  id,
  value,
  agents,
  disabled,
  title,
  onChange,
}: {
  id: string;
  value: string;
  agents: AgentInfo[];
  disabled?: boolean;
  title: string;
  onChange: (name: string) => void;
}) {
  return (
    <Row label="Agent" htmlFor={id}>
      <select id={id} value={value} disabled={disabled} title={title} onChange={(event) => onChange(event.target.value)}>
        <option value="">YAAP</option>
        {agents.map((agent) => (
          <option key={agent.name} value={agent.name}>
            {agent.name}
          </option>
        ))}
        {value && !agents.some((agent) => agent.name === value) && <option value={value}>{value} (missing)</option>}
      </select>
    </Row>
  );
}

/** The context in use against the limit, with a bar; in a conversation the breakdown opens below it. */
function ContextRow({ use, details }: { use: ContextUse; details?: ReactNode }) {
  const [shown, setShown] = useState(false);
  const state = use.state === "full" ? " context-full" : use.state === "filling" ? " context-filling" : "";
  return (
    <div className={`model-setup-context${state}`}>
      <div className="model-setup-row">
        <span>Context</span>
        <span className="model-setup-control mono">
          <span>{`${formatTokens(use.tokens)} / ${formatTokens(use.limit ?? 0)}`}</span>
          {use.lowerThanWindow && <span className="faint">of {formatTokens(use.window ?? 0)}</span>}
        </span>
      </div>
      <span className="meter model-setup-meter">
        <span style={{ width: `${Math.max(use.fraction * 100, use.tokens ? 1 : 0)}%` }} />
      </span>
      {details && (
        <>
          <button
            type="button"
            className="link-button small model-setup-details"
            aria-expanded={shown}
            onClick={() => setShown((value) => !value)}
          >
            {shown ? "Hide where it goes" : "Where it goes"}
          </button>
          {shown && <div className="model-setup-breakdown">{details}</div>}
        </>
      )}
    </div>
  );
}

export interface ConversationRowsProps {
  conversation: Conversation;
  use: ContextUse;
  agents: AgentInfo[];
  /** The display name of the conversation's model, when the environment offers it. */
  modelName: string | undefined;
  busy: boolean;
  onChangeAgent: (name: string) => void;
  /** The conversation changed on the server (its context limit, a summary): load it again. */
  onReload: () => void;
  /** Send /compact, as when it is typed. */
  onCompact: () => void;
  onError: (message: string) => void;
}

/**
 * A conversation's setup: the agent from the next message on, the model and effort it started with (they are fixed
 * for the conversation, as in the header before), its context, the limit, and Compact now.
 */
export function ConversationRows(props: ConversationRowsProps) {
  const { conversation, use, busy } = props;
  const fixed = "Fixed for this conversation; a new conversation can start with another";
  return (
    <>
      <AgentRow
        id="setup-agent"
        value={conversation.agent ?? ""}
        agents={props.agents}
        disabled={busy}
        title="The agent this conversation works as, from the next message on"
        onChange={props.onChangeAgent}
      />
      <Row label="Model">
        <span className="model-setup-value" title={fixed}>
          {props.modelName ?? conversation.model}
        </span>
      </Row>
      {conversation.effort && (
        <Row label="Effort">
          <span className="model-setup-value" title={fixed}>
            {conversation.effort}
          </span>
        </Row>
      )}
      <ContextRow
        use={use}
        details={
          <ContextBreakdown
            conversationId={conversation.id}
            use={use}
            control={conversation.context_control ?? null}
            version={conversation.messages.length}
            onSummarised={props.onReload}
          />
        }
      />
      <Row label="Limit">
        <ContextLimitSelect
          id="setup-context-limit"
          plain
          value={conversation.context_limit ?? null}
          window={conversation.context_window}
          conversationId={conversation.id}
          disabled={busy}
          onChange={props.onReload}
          onError={props.onError}
        />
      </Row>
      <Row label="Compact now">
        <button
          type="button"
          className="button button-ghost mono model-setup-compact"
          disabled={busy || conversation.messages.length === 0}
          title="Summarise the earlier messages now, as typing /compact does; /compact <focus> says what to keep"
          onClick={props.onCompact}
        >
          /compact
        </button>
      </Row>
    </>
  );
}

export interface DraftRowsProps {
  draft: DraftConversation;
  use: ContextUse;
  models: ModelInfo[];
  presets: Preset[];
  agents: AgentInfo[];
}

/**
 * What a new conversation will start with: the agent (which may bring its preset), the preset, or a model and effort
 * of the user's choice (choosing either makes the preset Custom), and the context limit.
 */
export function DraftRows({ draft, use, models, presets, agents }: DraftRowsProps) {
  const efforts = draft.modelInfo?.efforts ?? [];
  return (
    <>
      <AgentRow
        id="setup-agent"
        value={draft.agent}
        agents={agents}
        title="The agent the new conversation works as"
        onChange={draft.chooseAgent}
      />
      <Row label="Preset" htmlFor="setup-preset">
        <select id="setup-preset" value={draft.preset} onChange={(event) => draft.choosePreset(event.target.value)}>
          {presets.map((preset) => {
            const model = models.find((item) => item.id === preset.model);
            return (
              <option
                key={preset.id}
                value={String(preset.id)}
                title={model?.unavailable_reason ?? `${model?.display_name ?? preset.model} · ${preset.effort}`}
              >
                {preset.name}
                {model && !model.available ? " (no key)" : ""}
              </option>
            );
          })}
          <option value="custom">Custom</option>
        </select>
      </Row>
      <Row label="Model" htmlFor="setup-model">
        <select
          id="setup-model"
          value={draft.model}
          onChange={(event) => {
            draft.choosePreset("custom");
            draft.chooseModel(event.target.value);
          }}
        >
          <ModelOptions models={models} current={draft.model} />
        </select>
      </Row>
      {efforts.length > 0 && (
        <Row label="Effort" htmlFor="setup-effort">
          <select
            id="setup-effort"
            value={draft.effort}
            onChange={(event) => {
              draft.choosePreset("custom");
              draft.setEffort(event.target.value);
            }}
          >
            {efforts.map((effort) => (
              <option key={effort} value={effort}>
                {effort}
              </option>
            ))}
          </select>
        </Row>
      )}
      <ContextRow use={use} />
      <Row label="Limit">
        <ContextLimitSelect
          id="setup-context-limit"
          plain
          value={draft.limit}
          window={draft.modelInfo?.context_window}
          onChange={draft.setLimit}
        />
      </Row>
    </>
  );
}
