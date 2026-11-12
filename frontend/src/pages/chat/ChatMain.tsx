import { type RefObject, useState } from "react";

import type { BlueprintItem, Conversation, ConversationSummary, Environment, Project } from "../../api/client";
import Composer from "../../components/Composer";
import GoalStrip from "../../components/GoalStrip";
import type { CheckpointLinks } from "../../components/message/contexts";
import type { MessageActions } from "../../components/message/MessageActions";
import { modelState } from "../../components/modelState";
import QueueStrip from "../../components/QueueStrip";
import TraceDialog from "../../components/trace/TraceDialog";
import type { ToolCallRef } from "../../components/trace/traceOrder";
import type { TraceTarget } from "../../components/trace/useTrace";
import { UtilitySkipNotice } from "../../components/UtilitySkipNotice";
import ChatHeading from "./ChatHeading";
import { ContextNotice, ModelNotice, ReturnNoticeFor } from "./ChatNotices";
import { AccessSelect, TargetSelect } from "./ComposerControls";
import EvalCaseAction from "./EvalCaseAction";
import LearningAction from "./LearningAction";
import MessageList from "./MessageList";
import ModelSetupButton from "./ModelSetupButton";
import { previewScreenshotFile } from "./previewShot";
import type { ActiveConversation } from "./useActiveConversation";
import type { ChatCatalog } from "./useChatCatalog";
import type { ChatSession } from "./useChatSession";
import { type AgentInfo, useCommands } from "./useCommands";
import type { DraftConversation } from "./useDraftConversation";
import { useGoalEstimate } from "./useGoalEstimate";
import { type DraftRequest, useComposerDraft } from "./useComposerDraft";
import type { ProjectRail } from "./useProjectRail";
import { useSecondOpinions } from "./useSecondOpinions";

export interface ChatMainProps {
  environment: Environment;
  catalog: ChatCatalog;
  active: ActiveConversation;
  draft: DraftConversation;
  session: ChatSession;
  /** The panels beside the chat: which one is open, and the counts on the rail. */
  rail: ProjectRail;
  offer: { agents: AgentInfo[]; items: BlueprintItem[] };
  /** The project chosen in the sidebar. */
  activeProject: Project | null;
  /** The open conversation's project. */
  conversationProject: Project | null;
  /** The project the conversation works in; for a new conversation, the one chosen in the sidebar. */
  projectId: number | null;
  running: number[];
  focusMessageId: number | null;
  checkpointLinks: CheckpointLinks | null;
  endRef: RefObject<HTMLDivElement | null>;
  onNewConversation: () => void;
  /** Open the conversation made from a summary of the open one. */
  onContinued: (conversation: ConversationSummary) => void;
}

/** The middle of the chat: heading, notices, the messages, and the composer with the goal and queue above it. */
export default function ChatMain(props: ChatMainProps) {
  const { environment, catalog, active, draft, session } = props;
  const conversation = active.conversation;
  const conversationModel = catalog.models.find((model) => model.id === conversation?.model);
  // The model in use decides: its connection needs a key, or none (Claude Code; when that is not signed in, the
  // reply says how to sign in).
  const activeModel = conversation ? conversationModel : draft.modelInfo;
  const canChat = catalog.models.length === 0 || !activeModel || modelState(activeModel).kind === "ok";
  const [trace, setTrace] = useState<TraceTarget | null>(null);
  const goal = session.goal.shown;
  const goalEstimate = useGoalEstimate(
    props.projectId,
    (conversation ? conversation.agent : draft.agent) || null,
    `${goal?.id ?? ""}:${goal?.status ?? ""}`,
  );
  const lastReplyId = conversation ? lastReplyOf(conversation) : null;
  const showTrace = (messageId: number, toolCall?: ToolCallRef, tab?: "route", stepId?: number) =>
    conversation && setTrace({ conversationId: conversation.id, messageId, toolCall, tab, stepId });
  const composerDraft = useComposerDraft(conversation?.id ?? null);
  const secondOpinions = useSecondOpinions(conversation?.id ?? null);
  const actions: MessageActions = {
    ...messageActions(props),
    secondOpinions: secondOpinions ?? undefined,
    onUseInConversation: composerDraft.fill,
    onOpenReview: props.rail.focusReview,
    onShowTrace: (message, toolCall, tab, stepId) => showTrace(message.id, toolCall, tab, stepId),
    lastReply:
      conversation && lastReplyId !== null ? lastReplyActions(conversation, lastReplyId, session.busy) : undefined,
  };
  return (
    <section className="chat-main">
      <MainHeading {...props} lastReplyId={lastReplyId} onShowTrace={(messageId) => showTrace(messageId)} />
      {!canChat && activeModel && (
        <ModelNotice model={activeModel} environment={environment} hasConversation={conversation !== null} />
      )}
      {conversation && <ContextNotice conversation={conversation} onNewConversation={props.onNewConversation} />}
      {conversation && (
        <ReturnNoticeFor
          conversation={conversation}
          busy={session.busy}
          onChanged={() => void active.load(conversation.id)}
          onContinued={props.onContinued}
        />
      )}
      <MessageList
        conversation={conversation}
        streaming={session.reply.blocks}
        routeStops={session.stages.stops}
        focusMessageId={props.focusMessageId}
        actions={actions}
        checkpointLinks={props.checkpointLinks}
        environmentName={environment.name}
        activeProject={props.activeProject}
        error={active.error}
        endRef={props.endRef}
        onContinue={() => conversation && void session.stream.sendText(conversation.id, "Continue", [])}
        onRetryMessage={(messageId) => {
          const message = conversation?.messages.find((entry) => entry.id === messageId);
          if (message) session.stream.retryStoredMessage(message);
        }}
        onOutsideDecided={() => conversation && void active.load(conversation.id)}
      />
      {conversation && <UtilitySkipNotice skips={session.skips.shown} onDismiss={session.skips.dismiss} />}
      {conversation && goal && (
        <GoalStrip
          goal={goal}
          estimate={goalEstimate}
          onChange={(body) =>
            void session.goal.change(conversation.id, body).then((resumed) => {
              // Resuming sends the next turn on the server: follow it.
              if (resumed) void session.stream.followRunning(conversation.id);
            })
          }
          onDismiss={session.goal.dismiss}
        />
      )}
      {canChat && <ChatComposer {...props} goalEstimate={goalEstimate} composerDraft={composerDraft.request} />}
      {trace && <TraceDialog target={trace} onClose={() => setTrace(null)} />}
    </section>
  );
}

/** The heading of the open conversation; the agent, the model and the context sit in the model setup at the composer. */
function MainHeading(props: ChatMainProps & { lastReplyId: number | null; onShowTrace: (messageId: number) => void }) {
  const { active, rail, session } = props;
  const conversation = active.conversation;
  const done = Promise.resolve(null);
  return (
    <ChatHeading
      conversation={conversation}
      project={conversation ? props.conversationProject : props.activeProject}
      git={{
        branch: rail.git.repository?.branch ?? null,
        changedFiles: rail.counts.changedFiles,
        ahead: rail.git.repository?.ahead ?? 0,
      }}
      onOpenPanel={(panel) => rail.open(panel)}
      busy={session.busy}
      lastReplyId={props.lastReplyId}
      onShowTrace={props.onShowTrace}
      onRename={(title) => (conversation ? active.update(conversation.id, { title }) : done)}
      onArchive={(archived) => (conversation ? active.update(conversation.id, { archived }) : done)}
      onDelete={() => (conversation ? active.remove(conversation.id) : done)}
      projects={props.catalog.projects}
      onMove={(projectId) => (conversation ? active.move(conversation.id, projectId) : done)}
      onOpenReview={rail.focusReview}
    />
  );
}

/** The conversation's last stored reply, under which its own actions sit; null before the first one. */
function lastReplyOf(conversation: Conversation): number | null {
  const replies = conversation.messages.filter((message) => message.role === "assistant" && message.id > 0);
  return replies.at(-1)?.id ?? null;
}

/** Save as eval case and Learn from this act on the whole conversation, so they sit under its last reply only. */
function lastReplyActions(conversation: Conversation, messageId: number, busy: boolean): MessageActions["lastReply"] {
  const projectId = conversation.project_id;
  if (projectId == null) return undefined;
  return {
    messageId,
    actions: (
      <>
        <EvalCaseAction conversationId={conversation.id} projectId={projectId} disabled={busy} />
        <LearningAction key={conversation.id} conversationId={conversation.id} disabled={busy} />
      </>
    ),
  };
}

/** What a message can do: edit, regenerate, switch versions, and remember as a rule. */
function messageActions({ environment, active, session, conversationProject }: ChatMainProps): MessageActions {
  const conversation = active.conversation;
  return {
    busy: session.busy,
    ruleTarget: conversation
      ? {
          conversationId: conversation.id,
          environmentId: environment.id,
          environmentName: environment.name,
          projectId: conversation.project_id ?? null,
          projectName: conversationProject?.name ?? null,
        }
      : undefined,
    onEdit: session.stream.edit,
    onRegenerate: session.stream.regenerate,
    onShowVersion: (messageId) => void active.showVersion(messageId),
  };
}

/** The queue strip and the composer, with the controls of a new conversation and its access. */
function ChatComposer({
  environment,
  catalog,
  active,
  draft,
  session,
  offer,
  projectId,
  rail,
  running,
  goalEstimate,
  composerDraft,
}: ChatMainProps & { goalEstimate: string | null; composerDraft: DraftRequest | null }) {
  const conversation = active.conversation;
  const { queue, composer, stream, attachments } = session;
  // A running preview can be attached as a screenshot.
  const previewProcess = rail.preview.processId;
  const agent = offer.agents.find((item) => item.name === (conversation ? conversation.agent : draft.agent));
  const { commands, skills } = useCommands(offer.items, agent, projectId !== null, goalEstimate);
  return (
    <>
      {conversation && (
        <QueueStrip
          items={queue.items}
          onSendNow={(item) => void stream.sendQueuedNow(item)}
          onSendAll={() => queue.items[0] && void stream.sendQueuedNow(queue.items[0], true)}
          onRemove={(item) => void queue.remove(item)}
          onSteer={running.includes(conversation.id) ? (item) => void queue.steer(item) : undefined}
        />
      )}
      <Composer
        draft={composerDraft}
        busy={session.busy}
        onSend={(text) => void composer.send(text)}
        attachments={attachments.here}
        onAddFiles={(files) => void composer.addFiles(files)}
        onRemoveAttachment={attachments.remove}
        commands={commands}
        skills={skills}
        pinnedSkills={conversation ? (conversation.pinned_skills ?? []) : draft.pinned}
        onPinnedSkillsChange={(chosen) =>
          conversation ? void active.changePinnedSkills(chosen) : draft.setPinned(chosen)
        }
        speech={{ environmentId: environment.id, projectId }}
        onQueue={(text) => void composer.queueText(text)}
        onSteer={(text) => void composer.steer(text)}
        onStop={stream.stop}
        projectId={projectId}
        previewShot={
          conversation && previewProcess !== null
            ? () => previewScreenshotFile(conversation.id, previewProcess)
            : undefined
        }
        setup={
          <ModelSetupButton
            active={active}
            draft={draft}
            catalog={catalog}
            agents={offer.agents}
            busy={session.busy}
            onCompact={() => void composer.send("/compact")}
          />
        }
        controls={
          <>
            {!conversation && projectId === null && <TargetSelect draft={draft} targets={catalog.targets} />}
            <AccessSelect
              value={conversation ? (conversation.access ?? "default") : draft.access}
              onChange={(access) => (conversation ? void active.changeAccess(access) : draft.setAccess(access))}
            />
          </>
        }
      />
    </>
  );
}
