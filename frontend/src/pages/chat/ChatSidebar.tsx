import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import type { ConversationSummary, Project } from "../../api/client";
import ConversationRow from "../../components/ConversationRow";
import { ArchiveIcon, FileIcon, PlusIcon } from "../../components/icons";
import ImportSessionDialog from "../../components/ImportSessionDialog";
import PreferencesMenu from "../../components/PreferencesMenu";
import ProjectPicker from "./ProjectPicker";
import UsageCard from "./UsageCard";
import type { ChatCatalog } from "./useChatCatalog";
import type { ConversationList } from "./useConversationList";
import type { ProjectScope } from "./useProjectScope";
import type { UsageSummary } from "./useUsage";

type ConversationGroup = { label: string; items: ConversationSummary[] };

function groupByDay(conversations: ConversationSummary[]): ConversationGroup[] {
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const startOfYesterday = new Date(startOfToday.getTime() - 86_400_000);
  const groups = [
    { label: "Today", items: [] as ConversationSummary[] },
    { label: "Yesterday", items: [] as ConversationSummary[] },
    { label: "Earlier", items: [] as ConversationSummary[] },
  ];
  for (const conversation of conversations) {
    const updated = new Date(conversation.updated_at);
    const group = updated >= startOfToday ? 0 : updated >= startOfYesterday ? 1 : 2;
    groups[group].items.push(conversation);
  }
  return groups.filter((group) => group.items.length > 0);
}

/** Rename, archive or delete a conversation of the list; each resolves to the error to show, or null. */
export interface ConversationListActions {
  onRename: (id: number, title: string) => Promise<string | null>;
  onArchive: (id: number, archived: boolean) => Promise<string | null>;
  onDelete: (id: number) => Promise<string | null>;
}

/** Model, effort and preset an imported session is continued with. */
export interface ImportDefaults {
  model: string;
  effort: string;
  preset: string | null;
}

interface ChatSidebarProps {
  environmentId: number;
  /** A reply streams in: a new conversation waits until it is done. */
  busy: boolean;
  catalog: ChatCatalog;
  list: ConversationList;
  scope: ProjectScope;
  running: number[];
  actions: ConversationListActions;
  onOpenProjectPanel: (project: Project) => void;
  importDefaults: ImportDefaults;
  usage: UsageSummary | null;
}

/** The chat's sidebar: new conversation, projects, the conversations of the chosen project, and usage. */
export default function ChatSidebar(props: ChatSidebarProps) {
  const { environmentId, catalog, list, scope } = props;
  const navigate = useNavigate();
  return (
    <aside className="chat-sidebar">
      <button type="button" className="button button-primary" onClick={() => navigate("/chat")} disabled={props.busy}>
        <PlusIcon />
        New conversation
      </button>
      <ProjectPicker
        environmentId={environmentId}
        projects={catalog.projects}
        scope={scope.scope}
        onChoose={(projectId) => {
          scope.choose(projectId);
          navigate("/chat");
        }}
        onOpenPanel={props.onOpenProjectPanel}
        onCreated={(project) => {
          catalog.addProject(project);
          scope.choose(project.id);
          navigate("/chat");
        }}
      />
      <ConversationGroups
        conversations={list.conversations}
        archived={list.archived}
        scope={scope.scope}
        running={props.running}
        actions={props.actions}
      />
      <ImportSession
        environmentId={environmentId}
        projects={catalog.projects}
        scope={scope.scope}
        defaults={props.importDefaults}
        onImported={(summary) => {
          list.remember(summary);
          navigate(`/chat/${summary.id}`);
        }}
      />
      <PreferencesMenu />
      <UsageCard usage={props.usage} />
    </aside>
  );
}

interface ConversationGroupsProps {
  conversations: ConversationSummary[];
  archived: ConversationSummary[];
  scope: number | null;
  running: number[];
  actions: ConversationListActions;
}

/** The conversations of the chosen project by day, or its archived ones. */
function ConversationGroups({ conversations, archived, scope, running, actions }: ConversationGroupsProps) {
  // The sidebar shows the archived conversations of the current scope instead of the active ones.
  const [showArchived, setShowArchived] = useState(false);
  const archivedInScope = useMemo(
    () => archived.filter((item) => (item.project_id ?? null) === scope),
    [archived, scope],
  );
  const groups = useMemo(
    () => groupByDay(conversations.filter((item) => (item.project_id ?? null) === scope)),
    [conversations, scope],
  );
  return (
    <>
      {(showArchived ? [{ label: "Archived", items: archivedInScope }] : groups).map((group) => (
        <div key={group.label}>
          <div className="sidebar-group-label">{group.label}</div>
          <ul className="conversation-list">
            {group.items.map((item) => (
              <ConversationRow
                key={item.id}
                item={item}
                running={running.includes(item.id)}
                onRename={(title) => actions.onRename(item.id, title)}
                onArchive={(isArchived) => actions.onArchive(item.id, isArchived)}
                onDelete={() => actions.onDelete(item.id)}
              />
            ))}
            {showArchived && archivedInScope.length === 0 && (
              <li className="muted small sidebar-empty">No archived conversations here.</li>
            )}
          </ul>
        </div>
      ))}
      {(showArchived || archivedInScope.length > 0) && (
        <button type="button" className="archive-toggle" onClick={() => setShowArchived(!showArchived)}>
          <ArchiveIcon size={14} />
          {showArchived ? "Back to conversations" : `Archived (${archivedInScope.length})`}
        </button>
      )}
    </>
  );
}

/** Import a Claude Code or Codex session. */
interface ImportSessionProps {
  environmentId: number;
  projects: Project[];
  scope: number | null;
  defaults: ImportDefaults;
  onImported: (summary: ConversationSummary) => void;
}

function ImportSession({ environmentId, projects, scope, defaults, onImported }: ImportSessionProps) {
  const [importing, setImporting] = useState(false);
  return (
    <>
      <button type="button" className="archive-toggle" onClick={() => setImporting(true)}>
        <FileIcon size={14} />
        Import a session
      </button>
      {importing && (
        <ImportSessionDialog
          environmentId={environmentId}
          projects={projects}
          initialProject={scope}
          model={defaults.model}
          effort={defaults.effort}
          preset={defaults.preset}
          onClose={() => setImporting(false)}
          onImported={(summary) => {
            setImporting(false);
            onImported(summary);
          }}
        />
      )}
    </>
  );
}
