import { useNavigate } from "react-router-dom";

import type { Environment } from "../api/client";
import BoardPage from "./board/BoardPage";
import ChatMain from "./chat/ChatMain";
import ChatSidebar from "./chat/ChatSidebar";
import ChatSidePanels from "./chat/ChatSidePanels";
import { useActiveConversation } from "./chat/useActiveConversation";
import { useChatCatalog, useChatModels } from "./chat/useChatCatalog";
import { useChatAddress, useChatLinks } from "./chat/useChatLinks";
import { useChatSession } from "./chat/useChatSession";
import { useBlueprintOffer } from "./chat/useCommands";
import { useConversationList } from "./chat/useConversationList";
import { useDraftConversation } from "./chat/useDraftConversation";
import { useMessageScroll } from "./chat/useMessageScroll";
import { useCheckpointLinks } from "./chat/useCheckpointLinks";
import { usePreviewPanel } from "./chat/usePreviewPanel";
import { useProjectRail, useRailSelection } from "./chat/useProjectRail";
import { useProjectScope } from "./chat/useProjectScope";
import { useRunningReplies } from "./chat/useRunningReplies";
import { useUsage } from "./chat/useUsage";

// The rail is always on screen in a project: its counts (and the header chip's) are read while the panels are closed.
const WATCH_RAIL_COUNTS = true;

/**
 * The chat: the sidebar, the open conversation with its composer, and the panels beside it. The page
 * only composes; each concern keeps its state in a hook of its own in `./chat/`.
 */
export default function ChatPage({ environment }: { environment: Environment }) {
  const navigate = useNavigate();
  const { selectedId, focusMessageId, boardProjectId, cardId } = useChatAddress();
  const basics = useChatCatalog(environment.id);
  const usage = useUsage(environment.id);
  const list = useConversationList(environment.id);
  const running = useRunningReplies(environment.id);
  const scope = useProjectScope(environment.id, basics.projects, basics.loaded);
  const selection = useRailSelection();
  const active = useActiveConversation({
    environmentId: environment.id,
    selectedId,
    onSummary: list.remember,
    onForget: list.forget,
    onProjectShown: scope.show,
  });
  const conversation = active.conversation;
  // The project the conversation works in; for a new conversation, the one chosen in the sidebar.
  const projectId = conversation ? (conversation.project_id ?? null) : scope.scope;
  // The models marked for that project, whose model policy can forbid some.
  const catalog = useChatModels(basics, environment.id, projectId);
  const offer = useBlueprintOffer(environment.id, projectId);
  // The preview beside the chat: the project's app, reloaded after each file change of the agent.
  const preview = usePreviewPanel(conversation?.project_id ?? null, conversation?.id ?? null, {
    open: selection.panel === "preview",
    close: () => selection.close("preview"),
    watch: WATCH_RAIL_COUNTS,
  });
  const draft = useDraftConversation({
    environmentId: environment.id,
    catalog,
    agents: offer.agents,
    scope: scope.scope,
    conversationId: conversation?.id ?? null,
    conversationSelected: selectedId !== null,
  });
  useChatLinks({ onProject: scope.choose, onPanel: selection.openLink, onAgent: draft.requestAgent });
  const session = useChatSession({
    environmentId: environment.id,
    selectedId,
    active,
    list,
    running,
    onReplyStored: () => void usage.reload(),
    startConversation: () => active.create(draft.newConversation()),
    onFileChanged: preview.notifyFileChanged,
    onEvalStarted: () => selection.open("evals"),
  });
  const checkpointLinks = useCheckpointLinks(conversation, session.busy, selection.focusCheckpoint);
  const endRef = useMessageScroll(conversation, session.reply.blocks, focusMessageId);

  const activeProject = catalog.projects.find((project) => project.id === scope.scope) ?? null;
  const conversationProject = conversation?.project_id
    ? (catalog.projects.find((project) => project.id === conversation.project_id) ?? null)
    : null;
  // The panel follows the open conversation's project, or the project chosen in the sidebar.
  const panelProject = conversation ? conversationProject : activeProject;
  // A reply may have changed files: the panels read the project again.
  const refreshKey = `${conversation?.id}-${conversation?.messages.length}-${session.busy}`;
  const rail = useProjectRail(selection, { project: panelProject, refreshKey, preview, watchCounts: WATCH_RAIL_COUNTS });

  return (
    <div className="chat">
      <ChatSidebar
        environmentId={environment.id}
        busy={session.busy}
        catalog={catalog}
        list={list}
        scope={scope}
        running={running.running}
        actions={{
          onRename: (id, title) => active.update(id, { title }),
          onArchive: (id, archived) => active.update(id, { archived }),
          onDelete: active.remove,
          onMove: active.move,
        }}
        onOpenProjectPanel={(project) => {
          scope.choose(project.id);
          if (conversation && conversation.project_id !== project.id) navigate("/chat");
          rail.open("settings");
        }}
        importDefaults={{ model: draft.model, effort: draft.effort, preset: draft.presetName }}
        usage={usage.usage}
      />
      {boardProjectId !== null && (
        <BoardPage
          projectId={boardProjectId}
          cardId={cardId}
          projects={catalog.projects}
          onScope={scope.choose}
          onProjectChanged={catalog.replaceProject}
        />
      )}
      {boardProjectId === null && <ChatMain
        environment={environment}
        catalog={catalog}
        active={active}
        draft={draft}
        session={session}
        rail={rail}
        offer={offer}
        activeProject={activeProject}
        conversationProject={conversationProject}
        projectId={projectId}
        running={running.running}
        focusMessageId={focusMessageId}
        checkpointLinks={checkpointLinks}
        endRef={endRef}
        onNewConversation={() => navigate("/chat")}
        onContinued={(continued) => {
          list.remember(continued);
          navigate(`/chat/${continued.id}`);
        }}
      />}
      <ChatSidePanels
        conversation={conversation}
        panelProject={panelProject}
        rail={rail}
        refreshKey={refreshKey}
        shared={session.shared}
        replyRunning={session.busy}
        onProjectChanged={catalog.replaceProject}
        onProjectRemoved={(project) => {
          rail.close();
          catalog.removeProject(project.id);
          scope.choose(null);
          navigate("/chat");
        }}
        onError={active.setError}
      />
    </div>
  );
}
