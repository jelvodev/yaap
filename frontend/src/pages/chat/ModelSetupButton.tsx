import { useCallback, useRef, useState } from "react";

import ContextRing from "../../components/composer/ContextRing";
import { contextText, contextUse, setupLabel, shortModelName } from "../../components/composer/modelSetup";
import { useDismiss } from "../../components/useDismiss";
import { ConversationRows, DraftRows } from "./ModelSetupRows";
import type { ActiveConversation } from "./useActiveConversation";
import type { ChatCatalog } from "./useChatCatalog";
import type { AgentInfo } from "./useCommands";
import type { DraftConversation } from "./useDraftConversation";

export interface ModelSetupProps {
  /** The open conversation, if any; without one the draft holds what a new conversation starts with. */
  active: ActiveConversation;
  draft: DraftConversation;
  catalog: ChatCatalog;
  agents: AgentInfo[];
  busy: boolean;
  /** Send /compact, as when it is typed. */
  onCompact: () => void;
}

/**
 * The model setup beside the message box: one button, `YAAP · Opus 5.5 · medium`, whose
 * icon's ring is the context meter; its popover holds the agent, preset, model, effort, context, limit and Compact
 * now. For a new conversation it sets what the conversation will start with.
 */
export default function ModelSetupButton({ active, draft, catalog, agents, busy, onCompact }: ModelSetupProps) {
  const conversation = active.conversation;
  const { models, presets } = catalog;
  const [open, setOpen] = useState(false);
  // Opened by the ring: the context breakdown is already shown (a keyboard user has the toggle in the popover).
  const [showBreakdown, setShowBreakdown] = useState(false);
  const box = useRef<HTMLDivElement | null>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismiss(box, open, close);

  const modelId = conversation ? conversation.model : draft.model;
  const modelInfo = models.find((model) => model.id === modelId);
  const label = setupLabel(
    conversation ? conversation.agent : draft.agent,
    shortModelName(modelInfo?.display_name, modelId),
    conversation ? conversation.effort : draft.effort,
  );
  const use = conversation
    ? contextUse(
        conversation.context_tokens,
        conversation.context_control?.limit ?? null,
        conversation.context_window,
      )
    : contextUse(null, draft.limit, draft.modelInfo?.context_window ?? null);
  const share = Math.round(use.fraction * 100);

  return (
    <div className="model-setup" ref={box}>
      <button
        type="button"
        className={`model-setup-button context-${use.state}`}
        aria-expanded={open}
        aria-haspopup="dialog"
        title={`Agent, model and context · context ${contextText(use)} (${share}%)`}
        onClick={() => {
          setShowBreakdown(false);
          setOpen((value) => !value);
        }}
      >
        <span
          className="model-setup-ring"
          title={conversation ? "Where the context goes" : undefined}
          onClick={(event) => {
            if (!conversation) return;
            event.stopPropagation();
            setOpen(!(open && showBreakdown));
            setShowBreakdown(true);
          }}
        >
          <ContextRing use={use} />
        </span>
        <span className="model-setup-label">{label}</span>
        <span className="model-setup-caret" aria-hidden="true">
          ▾
        </span>
      </button>
      {open && (
        <div className="model-setup-popover" role="dialog" aria-label="Model setup">
          {conversation ? (
            <ConversationRows
              key={showBreakdown ? "breakdown" : "rows"}
              conversation={conversation}
              breakdownShown={showBreakdown}
              use={use}
              agents={agents}
              modelName={modelInfo?.display_name}
              busy={busy}
              onChangeAgent={(name) => void active.changeAgent(name)}
              onReload={() => void active.load(conversation.id)}
              onCompact={() => {
                onCompact();
                close();
              }}
              onError={active.setError}
            />
          ) : (
            <DraftRows draft={draft} use={use} models={models} presets={presets} agents={agents} />
          )}
        </div>
      )}
    </div>
  );
}
