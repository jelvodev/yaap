import type { Conversation, Project } from "../../api/client";
import DocumentPanel from "../../components/document/DocumentPanel";
import type { useConversationDocument } from "../../components/document/useConversationDocument";
import PreviewPanel from "../../components/preview/PreviewPanel";
import ProjectPanel from "../../components/project/ProjectPanel";
import ProjectRail from "../../components/project/ProjectRail";
import type { ProjectRail as RailState } from "./useProjectRail";

interface ChatSidePanelsProps {
  conversation: Conversation | null;
  /** The project the rail's panels show: the open conversation's, or the one chosen in the sidebar. */
  panelProject: Project | null;
  /** Which panel is open, and the state the panels share with the rail's counts. */
  rail: RailState;
  /** Changes when a reply may have changed files, so the panels read again. */
  refreshKey: string;
  shared: ReturnType<typeof useConversationDocument>;
  replyRunning: boolean;
  onProjectChanged: (project: Project) => void;
  onProjectRemoved: (project: Project) => void;
  onError: (message: string) => void;
}

/**
 * The right side of the chat: the shared document, the open panel of the project rail (the preview of
 * the project's app or a project panel) and the rail itself on the right edge. Outside a project there
 * is no rail.
 */
export default function ChatSidePanels(props: ChatSidePanelsProps) {
  const { conversation, panelProject, rail, shared, replyRunning } = props;
  return (
    <>
      {rail.panel === "preview" && conversation?.project_id && (
        <PreviewPanel preview={rail.preview} conversationId={conversation.id} />
      )}
      {shared.document && conversation?.project_id && (
        <DocumentPanel
          key={`${conversation.id}-${shared.document.path}`}
          projectId={conversation.project_id}
          document={shared.document}
          version={shared.version}
          writing={shared.writing}
          delivered={shared.delivered}
          replyRunning={replyRunning}
          onDecide={shared.decide}
          onSaved={() => void shared.reload()}
          onClose={() => void shared.close()}
        />
      )}
      {panelProject && rail.projectTab && (
        <ProjectPanel
          key={panelProject.id}
          project={panelProject}
          tab={rail.projectTab}
          onTabChange={(tab) => void rail.openLink(tab)}
          onClose={() => rail.close()}
          refreshKey={props.refreshKey}
          git={rail.git}
          learning={rail.learning}
          checkpointFocus={rail.checkpointFocus}
          reviewFocus={rail.reviewFocus}
          onReviewStarted={rail.focusReview}
          onProjectChanged={props.onProjectChanged}
          onOpenDocument={
            conversation?.project_id === panelProject.id
              ? (path) => void shared.open(path).then((failure) => failure && props.onError(failure))
              : undefined
          }
          onProjectRemoved={() => props.onProjectRemoved(panelProject)}
        />
      )}
      {panelProject && <ProjectRail rail={rail} previewAvailable={Boolean(conversation?.project_id)} />}
    </>
  );
}
