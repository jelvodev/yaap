import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";
import CurrentState from "./CurrentState";
import { ProjectSkipNotice } from "../UtilitySkipNotice";

type Entries = components["schemas"]["MemoryEntriesOut"];
type Entry = components["schemas"]["MemoryEntryOut"];
type Suggestion = components["schemas"]["MemorySuggestionOut"];
type Merge = components["schemas"]["MemoryMergeOut"];
type Notice = { error: boolean; text: string } | null;

const STANDARD_SECTIONS = ["Decisions", "Conventions", "Lessons learned", "Notes"];

function EntryRow({
  entry,
  onSave,
  onDelete,
}: {
  entry: Entry;
  onSave: (text: string) => Promise<boolean>;
  onDelete: () => Promise<boolean>;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  async function run(action: () => Promise<boolean>) {
    setBusy(true);
    const done = await action();
    setBusy(false);
    if (done) {
      setDraft(null);
      setConfirming(false);
    }
  }

  if (draft !== null) {
    return (
      <li className="pp-entry pp-entry-editing">
        <textarea
          aria-label="Entry"
          value={draft}
          rows={3}
          autoFocus
          maxLength={1000}
          onChange={(event) => setDraft(event.target.value)}
        />
        <div className="pp-row">
          <button
            type="button"
            className="button button-primary"
            disabled={busy || !draft.trim() || draft.trim() === entry.text}
            onClick={() => void run(() => onSave(draft))}
          >
            Save
          </button>
          <button type="button" className="button button-ghost" onClick={() => setDraft(null)}>
            Cancel
          </button>
        </div>
      </li>
    );
  }
  return (
    <li className="pp-entry">
      <span className="pp-entry-text">{entry.text}</span>
      <span className="pp-entry-actions">
        {confirming ? (
          <>
            <button type="button" className="button button-danger" disabled={busy} onClick={() => void run(onDelete)}>
              Forget
            </button>
            <button type="button" className="button button-ghost" onClick={() => setConfirming(false)}>
              Keep
            </button>
          </>
        ) : (
          <>
            <button type="button" className="link-button small" onClick={() => setDraft(entry.text)}>
              Edit
            </button>
            <button type="button" className="link-button small" onClick={() => setConfirming(true)}>
              Forget…
            </button>
          </>
        )}
      </span>
    </li>
  );
}

function SuggestionRow({
  suggestion,
  onAccept,
  onDismiss,
}: {
  suggestion: Suggestion;
  onAccept: (text: string, section: string) => Promise<boolean>;
  onDismiss: () => Promise<boolean>;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const [section, setSection] = useState(suggestion.section);
  const [busy, setBusy] = useState(false);

  async function run(action: () => Promise<boolean>) {
    setBusy(true);
    await action();
    setBusy(false);
  }

  return (
    <li className="pp-entry pp-suggestion">
      {suggestion.updates_entry && (
        <span className="faint small">
          Updates: <span className="pp-suggestion-old">{suggestion.updates_entry}</span>
        </span>
      )}
      {draft !== null ? (
        <>
          <textarea aria-label="Suggested entry" value={draft} rows={3} maxLength={1000} onChange={(event) => setDraft(event.target.value)} />
          {!suggestion.updates_entry && (
            <select aria-label="Section" value={section} onChange={(event) => setSection(event.target.value)}>
              {STANDARD_SECTIONS.map((name) => (
                <option key={name}>{name}</option>
              ))}
            </select>
          )}
        </>
      ) : (
        <span className="pp-entry-text">
          <span className="faint small">{suggestion.section} · </span>
          {suggestion.text}
        </span>
      )}
      <span className="pp-entry-actions">
        <button
          type="button"
          className="button button-primary"
          disabled={busy || (draft !== null && !draft.trim())}
          onClick={() => void run(() => onAccept(draft ?? "", section))}
        >
          {suggestion.updates_entry ? "Update" : "Add"}
        </button>
        {draft === null && (
          <button type="button" className="link-button small" onClick={() => setDraft(suggestion.text)}>
            Edit
          </button>
        )}
        <button type="button" className="link-button small" disabled={busy} onClick={() => void run(onDismiss)}>
          Dismiss
        </button>
      </span>
    </li>
  );
}

/** A merge the utility model proposes: the entries it replaces and the merged text. */
function MergeRow({
  merge,
  onAccept,
  onReject,
}: {
  merge: Merge;
  onAccept: (text: string) => Promise<boolean>;
  onReject: () => Promise<boolean>;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(action: () => Promise<boolean>) {
    setBusy(true);
    await action();
    setBusy(false);
  }

  return (
    <li className="pp-entry pp-suggestion">
      <span className="faint small">Replaces {merge.entries.length} entries:</span>
      <ul className="pp-merge-old">
        {merge.entries.map((entry, index) => (
          <li key={index} className="pp-suggestion-old small">
            {entry.section && <span className="faint">{entry.section} · </span>}
            {entry.text}
          </li>
        ))}
      </ul>
      {draft !== null ? (
        <textarea aria-label="Merged entry" value={draft} rows={3} maxLength={1000} onChange={(event) => setDraft(event.target.value)} />
      ) : (
        <span className="pp-entry-text">
          <span className="faint small">{merge.section || "Without a section"} · </span>
          {merge.text}
        </span>
      )}
      <span className="pp-entry-actions">
        <button
          type="button"
          className="button button-primary"
          disabled={busy || (draft !== null && !draft.trim())}
          onClick={() => void run(() => onAccept(draft ?? ""))}
        >
          Merge
        </button>
        {draft === null && (
          <button type="button" className="link-button small" onClick={() => setDraft(merge.text)}>
            Edit
          </button>
        )}
        <button type="button" className="link-button small" disabled={busy} onClick={() => void run(onReject)}>
          Keep apart
        </button>
      </span>
    </li>
  );
}

/** The whole memory file as text, for edits the entries cannot express. */
function FileEditor({ projectId, onDone }: { projectId: number; onDone: () => void }) {
  const [memory, setMemory] = useState<string | null>(null);
  const [saved, setSaved] = useState("");
  const [notice, setNotice] = useState<Notice>(null);

  useEffect(() => {
    void api.GET("/api/v1/projects/{project_id}/memory", { params: { path: { project_id: projectId } } }).then(({ data }) => {
      setMemory(data?.content ?? "");
      setSaved(data?.content ?? "");
    });
  }, [projectId]);

  async function save(event: FormEvent) {
    event.preventDefault();
    const { data, error } = await api.PUT("/api/v1/projects/{project_id}/memory", {
      params: { path: { project_id: projectId } },
      body: { content: memory ?? "" },
    });
    if (error || !data) {
      setNotice({ error: true, text: errorMessage(error) });
      return;
    }
    onDone();
  }

  if (memory === null) return <p className="faint small">Loading…</p>;
  return (
    <form className="pp-memory-file" onSubmit={(event) => void save(event)}>
      <label className="visually-hidden" htmlFor="pp-memory">
        Project memory
      </label>
      <textarea id="pp-memory" className="mono" value={memory} onChange={(event) => setMemory(event.target.value)} />
      <div className="pp-row">
        <button type="submit" className="button button-primary" disabled={memory === saved}>
          Save memory
        </button>
        <button type="button" className="button button-ghost" onClick={onDone}>
          Back to entries
        </button>
      </div>
      {notice && <p className={notice.error ? "form-error small" : "muted small"}>{notice.text}</p>}
    </form>
  );
}

/**
 * The project memory as a dashboard: entries per section, search, edit and forget per entry, and new
 * entries. Every change is a commit of only .blueprint/memory.md in the project.
 */
export default function MemoryTab({ projectId }: { projectId: number }) {
  const [entries, setEntries] = useState<Entries | null>(null);
  const [filter, setFilter] = useState("");
  const [asFile, setAsFile] = useState(false);
  const [newSection, setNewSection] = useState(STANDARD_SECTIONS[0]);
  const [newText, setNewText] = useState("");
  const [notice, setNotice] = useState<Notice>(null);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [suggesting, setSuggesting] = useState(false);
  const [merges, setMerges] = useState<Merge[]>([]);
  const [tidying, setTidying] = useState(false);
  const path = { params: { path: { project_id: projectId } } };

  const loadMerges = useCallback(async () => {
    const { data } = await api.GET("/api/v1/projects/{project_id}/memory/merges", path);
    setMerges(data ?? []);
  }, [projectId]);

  useEffect(() => {
    void loadMerges();
  }, [loadMerges]);

  async function tidyUp() {
    setTidying(true);
    const { data, error } = await api.POST("/api/v1/projects/{project_id}/memory/tidy", path);
    setTidying(false);
    if (!data) {
      setNotice({ error: true, text: errorMessage(error) });
      return;
    }
    setMerges(data);
    setNotice({
      error: false,
      text: data.length ? `${data.length} merge${data.length === 1 ? "" : "s"} proposed.` : "Nothing to merge.",
    });
  }

  const loadSuggestions = useCallback(async () => {
    const { data } = await api.GET("/api/v1/projects/{project_id}/memory/suggestions", path);
    setSuggestions(data ?? []);
  }, [projectId]);

  useEffect(() => {
    void loadSuggestions();
  }, [loadSuggestions]);

  async function suggestNow() {
    setSuggesting(true);
    const { data, error } = await api.POST("/api/v1/projects/{project_id}/memory/suggestions", { ...path, body: {} });
    setSuggesting(false);
    if (!data) {
      setNotice({ error: true, text: errorMessage(error) });
      return;
    }
    setSuggestions(data);
    setNotice({ error: false, text: data.length ? `${data.length} suggestion${data.length === 1 ? "" : "s"} waiting.` : "Nothing new to suggest." });
  }

  const load = useCallback(async () => {
    const { data, error } = await api.GET("/api/v1/projects/{project_id}/memory/entries", path);
    if (data) setEntries(data);
    else setNotice({ error: true, text: errorMessage(error) });
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function apply(
    request: Promise<{ data?: Entries; error?: unknown; response: Response }>,
    done: string,
  ): Promise<boolean> {
    const { data, error, response } = await request;
    if (!data) {
      setNotice({ error: true, text: errorMessage(error) });
      if (response.status === 409) await load();
      return false;
    }
    setEntries(data);
    setNotice({ error: false, text: done });
    // A tidy-up may have run after a new entry, and an edit may have overtaken a proposed merge.
    void loadMerges();
    return true;
  }

  async function add(event: FormEvent) {
    event.preventDefault();
    const added = await apply(
      api.POST("/api/v1/projects/{project_id}/memory/entries", { ...path, body: { section: newSection, text: newText.trim() } }),
      "Added and committed in the project.",
    );
    if (added) setNewText("");
  }

  useEffect(() => {
    if (entries?.similar) setNotice({ error: false, text: `Added. It is close to an existing entry: “${entries.similar}”` });
  }, [entries]);

  const words = filter.toLowerCase().split(/\s+/).filter(Boolean);
  const shown = useMemo(
    () => entries?.entries.filter((entry) => words.every((word) => entry.text.toLowerCase().includes(word))) ?? [],
    [entries, filter],
  );

  if (asFile) {
    return (
      <div className="pp-memory">
        <FileEditor
          projectId={projectId}
          onDone={() => {
            setAsFile(false);
            void load();
          }}
        />
      </div>
    );
  }
  if (entries === null) return <p className="faint small pp-pad">{notice?.text ?? "Loading…"}</p>;
  return (
    <div className="pp-memory">
      <CurrentState projectId={projectId} />
      <p className="faint small">
        Decisions, conventions and lessons every conversation in this project knows. The agent adds to it with
        update_memory; each change is a commit of only <span className="mono">.blueprint/memory.md</span>. A large memory
        goes into the prompt in part: the newest entries and those closest to the message.
      </p>
      <ProjectSkipNotice projectId={projectId} tasks={["memory_tidy"]} />
      <div className="pp-row">
        <input
          type="search"
          aria-label="Search the memory"
          placeholder={`Search ${entries.entries.length} entries`}
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        />
        <button type="button" className="button button-ghost" onClick={() => setAsFile(true)}>
          Edit as file
        </button>
        <button
          type="button"
          className="button button-ghost"
          disabled={suggesting}
          title="Let the utility model read the latest conversation and propose entries"
          onClick={() => void suggestNow()}
        >
          {suggesting ? "Reading…" : "Suggest memory"}
        </button>
        <button
          type="button"
          className="button button-ghost"
          disabled={tidying || entries.entries.length < 2}
          title="Let the utility model propose merges of duplicate or overlapping entries; nothing changes until you accept one"
          onClick={() => void tidyUp()}
        >
          {tidying ? "Reading…" : "Tidy up"}
        </button>
      </div>
      {notice && <p className={notice.error ? "form-error small" : "muted small"}>{notice.text}</p>}
      {suggestions.length > 0 && (
        <section className="pp-memory-section">
          <h3>
            Suggestions <span className="faint small">{suggestions.length}</span>
          </h3>
          <ul className="pp-entries">
            {suggestions.map((suggestion) => (
              <SuggestionRow
                key={suggestion.id}
                suggestion={suggestion}
                onAccept={async (text, section) => {
                  const done = await apply(
                    api.POST("/api/v1/projects/{project_id}/memory/suggestions/{suggestion_id}/accept", {
                      params: { path: { project_id: projectId, suggestion_id: suggestion.id } },
                      body: { text: text.trim(), section },
                    }),
                    "Added and committed in the project.",
                  );
                  if (done) await loadSuggestions();
                  return done;
                }}
                onDismiss={async () => {
                  await api.DELETE("/api/v1/projects/{project_id}/memory/suggestions/{suggestion_id}", {
                    params: { path: { project_id: projectId, suggestion_id: suggestion.id } },
                  });
                  await loadSuggestions();
                  return true;
                }}
              />
            ))}
          </ul>
        </section>
      )}
      {merges.length > 0 && (
        <section className="pp-memory-section">
          <h3>
            Proposed merges <span className="faint small">{merges.length}</span>
          </h3>
          <ul className="pp-entries">
            {merges.map((merge) => (
              <MergeRow
                key={merge.id}
                merge={merge}
                onAccept={async (text) => {
                  const done = await apply(
                    api.POST("/api/v1/projects/{project_id}/memory/merges/{merge_id}/accept", {
                      params: { path: { project_id: projectId, merge_id: merge.id } },
                      body: { text: text.trim() },
                    }),
                    "Merged and committed in the project.",
                  );
                  if (!done) await loadMerges();
                  return done;
                }}
                onReject={async () => {
                  await api.DELETE("/api/v1/projects/{project_id}/memory/merges/{merge_id}", {
                    params: { path: { project_id: projectId, merge_id: merge.id } },
                  });
                  await loadMerges();
                  return true;
                }}
              />
            ))}
          </ul>
        </section>
      )}
      {entries.sections.map((section) => {
        const items = shown.filter((entry) => entry.section === section);
        if (!items.length && (words.length || !STANDARD_SECTIONS.includes(section))) return null;
        return (
          <section key={section || "-"} className="pp-memory-section">
            <h3>
              {section || "Without a section"} <span className="faint small">{items.length}</span>
            </h3>
            {items.length ? (
              <ul className="pp-entries">
                {items.map((entry) => (
                  <EntryRow
                    key={entry.id}
                    entry={entry}
                    onSave={(text) =>
                      apply(
                        api.PUT("/api/v1/projects/{project_id}/memory/entries/{entry_id}", {
                          params: { path: { project_id: projectId, entry_id: entry.id } },
                          body: { text: text.trim() },
                        }),
                        "Saved and committed in the project.",
                      )
                    }
                    onDelete={() =>
                      apply(
                        api.DELETE("/api/v1/projects/{project_id}/memory/entries/{entry_id}", {
                          params: { path: { project_id: projectId, entry_id: entry.id } },
                        }),
                        "Forgotten and committed in the project.",
                      )
                    }
                  />
                ))}
              </ul>
            ) : (
              <p className="faint small">Nothing yet.</p>
            )}
          </section>
        );
      })}
      {words.length > 0 && shown.length === 0 && <p className="faint small">No entry matches.</p>}
      <form className="pp-memory-add" onSubmit={(event) => void add(event)}>
        <h3>Add entry</h3>
        <select aria-label="Section" value={newSection} onChange={(event) => setNewSection(event.target.value)}>
          {STANDARD_SECTIONS.map((section) => (
            <option key={section}>{section}</option>
          ))}
        </select>
        <textarea
          aria-label="New entry"
          rows={2}
          maxLength={1000}
          value={newText}
          placeholder="One decision, convention or lesson"
          onChange={(event) => setNewText(event.target.value)}
        />
        <div className="pp-row">
          <button type="submit" className="button button-primary" disabled={!newText.trim()}>
            Add
          </button>
        </div>
      </form>
    </div>
  );
}
