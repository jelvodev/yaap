import {
  type ClipboardEvent,
  type DragEvent,
  type KeyboardEvent,
  type ReactNode,
  useRef,
  useState,
} from "react";

import { type PendingAttachment, formatSize } from "../api/attachments";
import { errorMessage } from "../api/client";
import { type FolderFile, droppedItems, readFolderEntry, zipFolder } from "../api/folders";
import AttachMenu from "./composer/AttachMenu";
import GoalButton from "./composer/GoalButton";
import { goalText } from "./composer/modelSetup";
import { ArchiveIcon, CloseIcon, FileIcon, FolderIcon, SendIcon, StopIcon } from "./icons";
import MicButton from "../speech/MicButton";
import type { SpeechScope } from "../speech/transcription";
import "./composer/composer.css";

export interface ComposerEntry {
  name: string;
  summary: string;
}

interface ComposerProps {
  busy: boolean;
  onSend: (text: string) => void;
  // While busy: keep the message for when the reply ends.
  onQueue?: (text: string) => void;
  // While busy: give the message to the agent now, with its next tool result.
  onSteer?: (text: string) => void;
  onStop: () => void;
  controls?: ReactNode;
  attachments: PendingAttachment[];
  onAddFiles: (files: File[]) => void;
  onRemoveAttachment: (localId: string) => void;
  commands: ComposerEntry[];
  skills: ComposerEntry[];
  pinnedSkills: string[];
  onPinnedSkillsChange: (skills: string[]) => void;
  // Speech input: where a recording is transcribed; without it there is no microphone.
  speech?: SpeechScope;
  // The model setup button, first in the bar.
  setup?: ReactNode;
  // The project whose files the Attach menu offers; without one it offers files and folders only.
  projectId?: number | null;
  // A screenshot of the running preview to attach; left out while no preview runs.
  previewShot?: () => Promise<File>;
}

function SkillsMenu({
  skills,
  pinned,
  onChange,
}: {
  skills: ComposerEntry[];
  pinned: string[];
  onChange: (skills: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  if (skills.length === 0) return null;
  return (
    <div className="skills-menu">
      <button
        type="button"
        className="attach-button skills-button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        title="Pin skills: their full text joins the conversation"
      >
        Skills{pinned.length ? ` · ${pinned.length}` : ""}
      </button>
      {open && (
        <div className="skills-popover" role="dialog" aria-label="Pin skills">
          {skills.map((skill) => (
            <label key={skill.name} className="skills-option">
              <input
                type="checkbox"
                checked={pinned.includes(skill.name)}
                onChange={(event) =>
                  onChange(
                    event.target.checked ? [...pinned, skill.name] : pinned.filter((name) => name !== skill.name),
                  )
                }
              />
              <span>
                <strong>{skill.name}</strong>
                {skill.summary && <span className="faint small"> {skill.summary}</span>}
              </span>
            </label>
          ))}
          <button type="button" className="button button-ghost" onClick={() => setOpen(false)}>
            Close
          </button>
        </div>
      )}
    </div>
  );
}

function PendingChip({ item, onRemove }: { item: PendingAttachment; onRemove: () => void }) {
  const kind = item.attachment?.kind;
  return (
    <div className={`pending-chip pending-${item.status}`} title={item.error ?? item.name}>
      {item.previewUrl ? (
        <img src={item.previewUrl} alt="" />
      ) : (
        <span className="pending-icon">
          {kind === "folder" ? <FolderIcon size={16} /> : kind === "archive" ? <ArchiveIcon size={16} /> : <FileIcon size={16} />}
        </span>
      )}
      <span className="pending-text">
        <span className="pending-name">{item.name}</span>
        <span className="pending-meta">
          {item.status === "uploading" ? "Adding…" : item.status === "error" ? item.error : formatSize(item.size)}
        </span>
      </span>
      <button type="button" className="pending-remove" onClick={onRemove} aria-label={`Remove ${item.name}`}>
        <CloseIcon size={12} />
      </button>
    </div>
  );
}

export default function Composer({
  busy,
  onSend,
  onQueue,
  onSteer,
  onStop,
  controls,
  attachments,
  onAddFiles,
  onRemoveAttachment,
  commands,
  skills,
  pinnedSkills,
  onPinnedSkillsChange,
  speech,
  setup,
  projectId,
  previewShot,
}: ComposerProps) {
  const [text, setText] = useState("");
  const [highlight, setHighlight] = useState(0);
  const [suggestionsClosed, setSuggestionsClosed] = useState(false);
  // Typing "/" at the start offers the commands; it closes once a space follows the name.
  const slash = /^\/([\w-]*)$/.exec(text);
  const suggestions =
    slash && !suggestionsClosed ? commands.filter((command) => command.name.startsWith(slash[1])).slice(0, 8) : [];
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLTextAreaElement | null>(null);
  // Folders are packed into one zip each before they upload.
  const [packing, setPacking] = useState(0);
  // Why a folder, a project file or a screenshot could not be attached.
  const [attachError, setAttachError] = useState<string | null>(null);
  // Goal mode is offered where /goal is.
  const goal = commands.find((command) => command.name === "goal");
  const uploading = packing > 0 || attachments.some((item) => item.status === "uploading");
  const ready = attachments.some((item) => item.status === "ready");
  const hasContent = !uploading && (Boolean(text.trim()) || ready);
  const canSend = !busy && hasContent;
  const canQueue = busy && Boolean(onQueue) && hasContent;
  const canSteer = busy && Boolean(onSteer) && Boolean(text.trim()) && !ready && !uploading;

  function steer() {
    if (!canSteer || !onSteer) return;
    onSteer(text.trim());
    setText("");
  }

  function submit() {
    if (canQueue && onQueue) {
      onQueue(text.trim());
      setText("");
      return;
    }
    if (!canSend) return;
    onSend(text.trim());
    setText("");
  }

  function focusInput() {
    const box = input.current;
    if (!box) return;
    box.focus();
    box.setSelectionRange(box.value.length, box.value.length);
  }

  function startGoal() {
    setText(goalText);
    setSuggestionsClosed(true);
    // After React has put the new text in the box, so the cursor lands after it.
    window.setTimeout(focusInput, 0);
  }

  function complete(name: string) {
    setText(`/${name} `);
    setSuggestionsClosed(false);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (suggestions.length > 0) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const step = event.key === "ArrowDown" ? 1 : -1;
        setHighlight((current) => (current + step + suggestions.length) % suggestions.length);
        return;
      }
      if (event.key === "Tab" || (event.key === "Enter" && !event.shiftKey)) {
        event.preventDefault();
        complete(suggestions[Math.min(highlight, suggestions.length - 1)].name);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setSuggestionsClosed(true);
        return;
      }
    }
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  }

  function handlePaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    const files = Array.from(event.clipboardData.files);
    if (files.length === 0) return;
    // A pasted screenshot becomes an attachment instead of text.
    event.preventDefault();
    onAddFiles(files);
  }

  async function addFolder(name: string, read: () => Promise<FolderFile[]>) {
    setAttachError(null);
    setPacking((count) => count + 1);
    try {
      onAddFiles([await zipFolder(name, await read())]);
    } catch (error) {
      setAttachError(errorMessage(error, `The folder ${name} could not be attached.`));
    } finally {
      setPacking((count) => count - 1);
    }
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    const { files, folders } = droppedItems(event.dataTransfer);
    if (files.length > 0) onAddFiles(files);
    for (const folder of folders) void addFolder(folder.name, () => readFolderEntry(folder));
  }

  return (
    <div
      className="composer-wrap"
      onDragOver={(event) => {
        if (Array.from(event.dataTransfer.types).includes("Files")) {
          event.preventDefault();
          setDragging(true);
        }
      }}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false);
      }}
      onDrop={handleDrop}
    >
      <div className={`composer${dragging ? " composer-dragging" : ""}`}>
        {dragging && <div className="drop-hint">Drop files or folders to attach them</div>}
        {suggestions.length > 0 && (
          <ul className="command-suggestions" role="listbox" aria-label="Commands">
            {suggestions.map((command, index) => (
              <li key={command.name} role="option" aria-selected={index === highlight}>
                <button
                  type="button"
                  onMouseDown={(event) => {
                    event.preventDefault();
                    complete(command.name);
                  }}
                >
                  <span className="mono">/{command.name}</span>
                  {command.summary && <span className="faint small">{command.summary}</span>}
                </button>
              </li>
            ))}
          </ul>
        )}
        {pinnedSkills.length > 0 && (
          <div className="pinned-list">
            {pinnedSkills.map((name) => (
              <span key={name} className="note-chip">
                Skill: {name}
                <button
                  type="button"
                  aria-label={`Unpin ${name}`}
                  onClick={() => onPinnedSkillsChange(pinnedSkills.filter((item) => item !== name))}
                >
                  <CloseIcon size={10} />
                </button>
              </span>
            ))}
          </div>
        )}
        {(packing > 0 || attachError) && (
          <p className={attachError ? "form-error small composer-note" : "faint small composer-note"}>
            {attachError ?? "Packing the folder…"}
          </p>
        )}
        {attachments.length > 0 && (
          <div className="pending-list">
            {attachments.map((item) => (
              <PendingChip key={item.localId} item={item} onRemove={() => onRemoveAttachment(item.localId)} />
            ))}
          </div>
        )}
        <label htmlFor="composer-input" className="visually-hidden">
          Message YAAP
        </label>
        <textarea
          id="composer-input"
          ref={input}
          value={text}
          onChange={(event) => {
            setText(event.target.value);
            setHighlight(0);
            setSuggestionsClosed(false);
          }}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          placeholder="Message YAAP… paste a screenshot or drop files here"
          rows={2}
          autoFocus
        />
        <div className="composer-bar">
          {/* The controls may wrap onto a second line; the send button stays at the right of the bar. */}
          <div className="composer-tools">
            {setup}
            <AttachMenu
              onFiles={onAddFiles}
              onFolder={(name, read) => void addFolder(name, read)}
              projectId={projectId}
              previewShot={previewShot}
              onError={setAttachError}
            />
            {speech && (
              <MicButton
                scope={speech}
                onText={(spoken) => {
                  // The transcription joins what is typed, for review before sending.
                  setText((current) => (current.trim() ? `${current.trimEnd()} ${spoken}` : spoken));
                  focusInput();
                }}
              />
            )}
            {goal && <GoalButton summary={goal.summary} onClick={startGoal} />}
            <SkillsMenu skills={skills} pinned={pinnedSkills} onChange={onPinnedSkillsChange} />
            {controls}
            <span className="composer-hint">
              {busy && onQueue ? "Enter to queue for after this reply" : "Enter to send"} · Shift+Enter for a new line · / for
              commands
            </span>
          </div>
          {busy && onSteer && (
            <button
              type="button"
              className="button"
              onClick={steer}
              disabled={!canSteer}
              title="Send this to YAAP now: it reads it with the result of its next action"
            >
              Steer
            </button>
          )}
          {busy ? (
            <button type="button" className="button stop-button" onClick={onStop}>
              <StopIcon size={14} />
              Stop
            </button>
          ) : (
            <button type="button" className="send-button" onClick={submit} disabled={!canSend} aria-label="Send">
              <SendIcon size={18} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
