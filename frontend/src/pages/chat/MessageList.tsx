import { Fragment, type RefObject } from "react";

import type { Conversation, Project, RouteStopOut } from "../../api/client";
import { Logo } from "../../components/Logo";
import { CheckpointContext, type CheckpointLinks, OutputContext } from "../../components/message/contexts";
import type { MessageActions } from "../../components/message/MessageActions";
import { ReplyBody } from "../../components/message/ReplyBody";
import type { ReplyBlock } from "../../components/message/replyBlock";
import MessageView from "../../components/MessageView";
import OutsideApprovals from "../../components/OutsideApprovals";
import { UtilityLine, utilityEventsAfter } from "../../components/PreferencesMenu";
import RouteStrip from "../../components/route/RouteStrip";
import CurrentStateCard from "./CurrentStateCard";
import { usePreferences } from "../../preferences/PreferencesProvider";

interface MessageListProps {
  conversation: Conversation | null;
  /** The reply streaming in, or null while none is followed. */
  streaming: ReplyBlock[] | null;
  /** The stops of the streaming reply's route so far. */
  routeStops: readonly RouteStopOut[];
  focusMessageId: number | null;
  actions: MessageActions;
  checkpointLinks: CheckpointLinks | null;
  environmentName: string;
  activeProject: Project | null;
  error: string | null;
  endRef: RefObject<HTMLDivElement | null>;
  /** Ask the agent to carry on where an incomplete reply stopped. */
  onContinue: () => void;
  /** Retry the reply to the latest stored user message without adding another message. */
  onRetryMessage: (messageId: number) => void;
  /** An approval asked outside the reply was decided: load the conversation again. */
  onOutsideDecided: () => void;
  /** Put text into the composer, not sent. */
  onFillDraft: (text: string) => void;
  /** Open the project's memory panel. */
  onEditCurrentState: () => void;
}

/** The messages of the open conversation and the reply streaming in, or the empty state of a new one. */
export default function MessageList(props: MessageListProps) {
  const { conversation, streaming } = props;
  const lastMessage = conversation?.messages.at(-1);
  return (
    <div className="messages">
      <div className="messages-inner">
        {!conversation && !streaming && (
          <EmptyState
            environmentName={props.environmentName}
            project={props.activeProject}
            onFillDraft={props.onFillDraft}
            onEditCurrentState={props.onEditCurrentState}
          />
        )}
        <OutputContext.Provider value={conversation?.id ?? null}>
          <CheckpointContext.Provider value={props.checkpointLinks}>
            {conversation && <StoredMessages {...props} conversation={conversation} />}
          </CheckpointContext.Provider>
          {streaming && (
            <div className="message-assistant">
              <div className="avatar" aria-hidden="true" />
              <div className="assistant-body">
                <RouteStrip stops={props.routeStops} />
                <ReplyBody blocks={streaming} streaming />
              </div>
            </div>
          )}
        </OutputContext.Provider>
        {conversation && <OutsideApprovals conversationId={conversation.id} onDecided={props.onOutsideDecided} />}
        {conversation && !streaming && lastMessage?.status === "incomplete" && (
          <div className="nudge">
            <button
              type="button"
              className="button"
              title="Ask YAAP to carry on where the reply stopped"
              onClick={props.onContinue}
            >
              Continue
            </button>
          </div>
        )}
        {conversation && !streaming && lastMessage?.role === "user" && (
          <div className="nudge">
            <button
              type="button"
              className="button"
              title="Retry the reply using this saved message"
              onClick={() => props.onRetryMessage(lastMessage.id)}
            >
              Retry reply
            </button>
          </div>
        )}
        {props.error && <div className="notice notice-error">{props.error}</div>}
        <div ref={props.endRef} />
      </div>
    </div>
  );
}

function StoredMessages({ conversation, focusMessageId, actions }: MessageListProps & { conversation: Conversation }) {
  const { preferences } = usePreferences();
  return conversation.messages.map((message, index) => (
    <Fragment key={message.id}>
      <MessageView message={message} focused={message.id === focusMessageId} actions={actions} />
      {preferences.utility_messages &&
        utilityEventsAfter(conversation, index).map((event) => (
          <UtilityLine key={`${event.task}-${event.created_at}`} event={event} />
        ))}
    </Fragment>
  ));
}

function EmptyState({
  environmentName,
  project,
  onFillDraft,
  onEditCurrentState,
}: {
  environmentName: string;
  project: Project | null;
  onFillDraft: (text: string) => void;
  onEditCurrentState: () => void;
}) {
  return (
    <div className="empty-state">
      <Logo blink className="empty-state-logo" />
      <h2>What are we building?</h2>
      <p className="muted">
        Working in <strong>{environmentName}</strong>
        {project && (
          <>
            {" "}
            · <strong>{project.name}</strong>
          </>
        )}
        . Press <kbd>Ctrl K</kbd> to jump anywhere.
      </p>
      {project && (
        <CurrentStateCard projectId={project.id} onContinue={onFillDraft} onEdit={onEditCurrentState} />
      )}
    </div>
  );
}
