import { useState } from "react";

import type { Conversation, Project } from "../../api/client";
import { TOOL_LABELS } from "../../components/ImportSessionDialog";
import ConversationMenu, { type ConversationMenuProps } from "./ConversationMenu";
import { changeChip, projectChipLabel } from "./headerChips";
import "./chatHeading.css";

/** The project's git state as the header's chips show it. */
export interface HeadingGit {
  /** The project's branch, once its git state is read. */
  branch: string | null;
  /** Files with uncommitted changes. */
  changedFiles: number;
  /** Commits that are not pushed. */
  ahead: number;
}

interface ChatHeadingProps
  extends Pick<ConversationMenuProps, "busy" | "lastReplyId" | "onShowTrace" | "onOpenReview"> {
  conversation: Conversation | null;
  /** The project the conversation works in; for a new conversation, the one chosen in the sidebar. */
  project: Project | null;
  git: HeadingGit;
  /** Open Project settings (the project chip) or Changes (the change chip) beside the chat. */
  onOpenPanel: (panel: "settings" | "changes") => void;
  /** Rename or archive the conversation; each resolves to the error to show, or null. */
  onRename: (title: string) => Promise<string | null>;
  onArchive: (archived: boolean) => Promise<string | null>;
  onDelete: () => Promise<string | null>;
}

/**
 * The heading above the messages: the title, where the conversation works (project, branch, computer), a chip while
 * the project's changes ask for something, and the ⋯ with everything else.
 */
export default function ChatHeading(props: ChatHeadingProps) {
  const { conversation } = props;
  const [renaming, setRenaming] = useState(false);
  return (
    <div className="chat-heading">
      {conversation && renaming ? (
        <RenameForm title={conversation.title} onRename={props.onRename} onDone={() => setRenaming(false)} />
      ) : (
        <h1>{conversation ? conversation.title : "New conversation"}</h1>
      )}
      <ProjectChip {...props} />
      {conversation?.imported && <ImportedChip imported={conversation.imported} />}
      {conversation?.archived_at && <span className="chip">Archived</span>}
      <span className="chat-heading-end">
        <ChangeChip {...props} />
        {conversation && (
          <ConversationMenu
            key={conversation.id}
            conversation={conversation}
            busy={props.busy}
            lastReplyId={props.lastReplyId}
            onShowTrace={props.onShowTrace}
            onRename={() => setRenaming(true)}
            onArchive={props.onArchive}
            onDelete={props.onDelete}
            onOpenReview={props.onOpenReview}
          />
        )}
      </span>
    </div>
  );
}

/** The project with its branch, and the computer over SSH; opens Project settings. */
function ProjectChip({ conversation, project, git, onOpenPanel }: ChatHeadingProps) {
  const target = conversation?.target ?? null;
  const fullControl = Boolean(target && conversation?.access === "full" && target.allow_full_access);
  const label = projectChipLabel({
    project: project?.name ?? null,
    branch: project ? git.branch : null,
    target: target?.name ?? null,
    fullControl,
  });
  if (!label) return null;
  const folder = target?.folder ? `, in ${target.folder}` : "";
  const where = target ? ` Works on ${target.name} over SSH${folder}; changes there cannot be undone in YAAP.` : "";
  const className = `chip chat-heading-project ${fullControl ? "chip-danger" : "chip-accent"}`;
  if (!project) {
    return (
      <span className={className} title={where.trim()}>
        {label}
      </span>
    );
  }
  return (
    <button
      type="button"
      className={className}
      title={`${project.folder}. Open Project settings.${where}`}
      onClick={() => onOpenPanel("settings")}
    >
      {label}
    </button>
  );
}

/** `5 changed · Review`, `1 to push · Sync` or both, while there is something to do; opens Changes. */
function ChangeChip({ project, git, onOpenPanel }: ChatHeadingProps) {
  const chip = project ? changeChip(git.changedFiles, git.ahead) : null;
  if (!chip) return null;
  return (
    <button
      type="button"
      className={`chip chat-heading-changes chat-heading-changes-${chip.kind}`}
      title={chip.title}
      onClick={() => onOpenPanel("changes")}
    >
      {chip.label}
    </button>
  );
}

function RenameForm({
  title,
  onRename,
  onDone,
}: {
  title: string;
  onRename: (title: string) => Promise<string | null>;
  onDone: () => void;
}) {
  const [value, setValue] = useState(title);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  async function save() {
    const next = value.trim();
    if (!next || next === title) return onDone();
    setBusy(true);
    const failure = await onRename(next);
    setBusy(false);
    if (failure) setProblem(failure);
    else onDone();
  }

  return (
    <form
      className="chat-heading-rename"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <label className="visually-hidden" htmlFor="conversation-title">
        Conversation name
      </label>
      <input
        id="conversation-title"
        value={value}
        maxLength={200}
        autoFocus
        disabled={busy}
        onFocus={(event) => event.target.select()}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => event.key === "Escape" && onDone()}
        onBlur={() => !busy && onDone()}
      />
      {problem && <span className="form-error small">{problem}</span>}
    </form>
  );
}

function ImportedChip({ imported }: { imported: NonNullable<Conversation["imported"]> }) {
  const where = imported.source.startsWith("target:") ? ` on ${imported.source.slice(7)}` : "";
  const ranIn = imported.cwd ? ` It ran in ${imported.cwd}.` : "";
  const counts = `${imported.turns} turns and ${imported.tool_calls} tool calls`;
  return (
    <span
      className="chip"
      title={`Imported from ${imported.file}${where}: ${counts}, shown as they were and never run again.${ranIn}`}
    >
      Imported from {TOOL_LABELS[imported.tool] ?? imported.tool}
    </span>
  );
}
