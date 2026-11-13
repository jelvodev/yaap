import { useState } from "react";

import type { Message } from "../../api/client";
import CardPart from "../card/CardPart";
import AttachmentList, { type AttachmentBlock } from "./AttachmentList";
import { type MessageActions, UserMessageActions } from "./MessageActions";

/** The element id and focus mark that a search result opens a message at. */
export interface MessageAnchor {
  id: string;
  "data-focused": true | undefined;
}

function UserMessageEditor({ initial, onSend, onCancel }: { initial: string; onSend: (text: string) => void; onCancel: () => void }) {
  const [text, setText] = useState(initial);
  return (
    <form
      className="message-edit"
      onSubmit={(event) => {
        event.preventDefault();
        if (text.trim()) onSend(text.trim());
      }}
    >
      <label className="visually-hidden" htmlFor="message-edit">
        Edit message
      </label>
      <textarea
        id="message-edit"
        value={text}
        rows={Math.min(12, Math.max(3, text.split("\n").length + 1))}
        autoFocus
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") onCancel();
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            if (text.trim()) onSend(text.trim());
          }
        }}
      />
      <div className="form-row">
        <button type="submit" className="button button-primary" disabled={!text.trim()}>
          Send as new version
        </button>
        <button type="button" className="button button-ghost" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

/** A message of the user: pinned skills, attachments, the text (or its editor) and the actions under it. A turn
 * that goal mode sent by itself shows as a marker instead. */
export default function UserMessage({
  message,
  anchor,
  actions,
}: {
  message: Message;
  anchor: MessageAnchor;
  actions?: MessageActions;
}) {
  const [editing, setEditing] = useState(false);
  const text = message.content
    .filter((block) => block.type === "text")
    .map((block) => block.text)
    .join("");
  const attachments = message.content.filter((block): block is AttachmentBlock => block.type === "attachment");
  const command = message.content.find((block) => block.type === "command");
  const cardId = command?.name === "card" ? (command.card_id ?? null) : null;
  const projectId = actions?.ruleTarget?.projectId ?? null;
  const goalTurn = message.content.find((block) => block.type === "goal");
  if (goalTurn && !text && (goalTurn.turn ?? 1) > 1) {
    // A turn YAAP sent by itself in goal mode: a marker, not a message of the user.
    return (
      <div className="message-notes" {...anchor} title={goalTurn.text}>
        <span className="note-chip">
          Goal · turn {goalTurn.turn} of {goalTurn.max_turns}
        </span>
      </div>
    );
  }
  const note = message.content.find((block) => block.type === "note");
  if (note && !text) {
    return (
      <div className="message-notes" {...anchor}>
        <span className="note-chip">{note.text}</span>
      </div>
    );
  }
  const skillNotes = message.content.filter((block) => block.type === "skill" || block.type === "skill_removed");
  return (
    <div className="message-user-group" {...anchor}>
      {skillNotes.length > 0 && (
        <div className="message-notes">
          {skillNotes.map((block) => (
            <span key={`${block.type}-${block.name}`} className="note-chip">
              {block.type === "skill" ? "Skill pinned" : "Skill unpinned"}: {block.name}
            </span>
          ))}
        </div>
      )}
      {attachments.length > 0 && <AttachmentList blocks={attachments} />}
      {editing && actions ? (
        <UserMessageEditor
          initial={text}
          onCancel={() => setEditing(false)}
          onSend={(newText) => {
            setEditing(false);
            actions.onEdit(message, newText);
          }}
        />
      ) : (
        text && (
          <div className="message-user" title={command ? `Command /${command.name}, sent as:\n\n${command.text}` : undefined}>
            {text}
          </div>
        )
      )}
      {cardId !== null && projectId !== null && <CardPart projectId={projectId} cardId={cardId} />}
      {actions && (
        <UserMessageActions
          message={message}
          text={text}
          actions={actions}
          editing={editing}
          onEdit={() => setEditing(true)}
        />
      )}
    </div>
  );
}
