import { useState } from "react";

import type { components } from "../api/schema";
import { usePreferences } from "../preferences/PreferencesProvider";
import { ChevronIcon } from "./icons";
import { canReadAloud, setReadEveryReply, useReadAloud } from "../speech/readAloud";

type Conversation = components["schemas"]["ConversationOut"];
type UtilityEvent = components["schemas"]["UtilityEventOut"];

const OPEN_STORAGE_KEY = "yaap-preferences-open";

function readOpen(): boolean {
  try {
    return window.localStorage.getItem(OPEN_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

/** The Preferences section at the bottom of the chat sidebar, closed until opened. */
export default function PreferencesMenu() {
  const { preferences, update } = usePreferences();
  const readAloud = useReadAloud();
  const [open, setOpen] = useState(readOpen);

  function toggle() {
    const next = !open;
    setOpen(next);
    try {
      window.localStorage.setItem(OPEN_STORAGE_KEY, String(next));
    } catch {
      // Remembering is a convenience only.
    }
  }

  return (
    <div className="preferences">
      <button type="button" className="preferences-head" aria-expanded={open} onClick={toggle}>
        <ChevronIcon size={12} />
        Preferences
      </button>
      {open && (
        <div className="preferences-body">
          <Switch
            label="Dark mode"
            checked={preferences.theme === "dark"}
            onChange={(checked) => void update({ theme: checked ? "dark" : "light" })}
          />
          <Switch
            label="Show utility messages"
            hint="Titles and other small tasks, with model, tokens and cost"
            checked={preferences.utility_messages}
            onChange={(checked) => void update({ utility_messages: checked })}
          />
          <Switch
            label="Show verbose tool calls"
            hint="Every tool call open, with its full input and output"
            checked={preferences.verbose_tools}
            onChange={(checked) => void update({ verbose_tools: checked })}
          />
          {canReadAloud() && (
            <Switch
              label="Read every reply aloud"
              hint="In this browser, with its own voices; code blocks and tool calls are skipped"
              checked={readAloud.everyReply}
              onChange={setReadEveryReply}
            />
          )}
        </div>
      )}
    </div>
  );
}

function Switch({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="preference" title={hint}>
      <span>{label}</span>
      <input type="checkbox" role="switch" checked={checked} onChange={(event) => onChange(event.target.checked)} />
    </label>
  );
}

const TASK_LABELS: Record<string, string> = {
  title: "Title set",
  title_and_memory: "Title set and memory suggested",
  commit_message: "Commit message proposed",
  import: "Import proposed",
  summary: "Summary made",
  injection_check: "Injection check",
};

/** The utility calls made after this message and before the next one. */
export function utilityEventsAfter(conversation: Conversation, index: number): UtilityEvent[] {
  const events = conversation.utility_events ?? [];
  const messages = conversation.messages;
  const from = new Date(messages[index].created_at).getTime();
  const next = messages[index + 1];
  const until = next ? new Date(next.created_at).getTime() : Number.POSITIVE_INFINITY;
  return events.filter((event) => {
    const at = new Date(event.created_at).getTime();
    return at >= from && at < until;
  });
}

/** A small muted line for one utility call (Preferences: show utility messages). */
export function UtilityLine({ event }: { event: UtilityEvent }) {
  const label = TASK_LABELS[event.task] ?? event.task;
  const cost = event.cost === null ? "subscription" : `$${event.cost.toFixed(4)}`;
  return (
    <div className="utility-line">
      <span>
        {label}
        {event.summary ? `: ${event.summary}` : ""}
      </span>
      <span className="utility-line-meta">
        {event.model} · {event.input_tokens} in / {event.output_tokens} out · {cost}
      </span>
    </div>
  );
}
