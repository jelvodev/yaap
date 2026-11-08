import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";

import { formatSize } from "../../api/attachments";
import { api, errorMessage } from "../../api/client";
import { changedAgo } from "../../api/files";
import type { components } from "../../api/schema";
import FileEditor from "../editor/FileEditor";
import { ChevronIcon, FileIcon, FolderIcon } from "../icons";
import { Segmented } from "../workbench/Workbench";

type Entry = components["schemas"]["FileEntryOut"];
type Location = components["schemas"]["FilesLocationOut"];
type Search = components["schemas"]["FileSearchOut"];
type SearchMode = "name" | "content";

interface FolderState {
  entries: Entry[];
  truncated: boolean;
}

function EntryMeta({ entry }: { entry: Entry }) {
  return (
    <span className="ft-meta faint" title={entry.modified ? new Date(entry.modified).toLocaleString() : undefined}>
      {!entry.is_dir && <span>{formatSize(entry.size)}</span>}
      <span>{changedAgo(entry.modified)}</span>
    </span>
  );
}

/**
 * The project's files where its conversations work, as a tree that loads one folder at a time, with search by name
 * or content. `.git` and dependency folders are left out, as for the agent.
 */
export function FileTree({
  projectId,
  refreshKey,
  onOpen,
  selected = null,
}: {
  projectId: number;
  /** Changes when a reply may have changed files, so the open folders are read again. */
  refreshKey?: unknown;
  /** Open a file, at a line for a content match; without it files are only listed. */
  onOpen?: (path: string, line?: number) => void;
  selected?: string | null;
}) {
  const [folders, setFolders] = useState<Map<string, FolderState>>(new Map());
  const [expanded, setExpanded] = useState<Set<string>>(new Set(["."]));
  const [location, setLocation] = useState<Location | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<SearchMode>("name");
  const [results, setResults] = useState<Search | null>(null);
  const [searching, setSearching] = useState(false);
  const expandedRef = useRef(expanded);
  expandedRef.current = expanded;

  const loadFolder = useCallback(
    async (path: string) => {
      const { data, error: failure } = await api.GET("/api/v1/projects/{project_id}/files", {
        params: { path: { project_id: projectId }, query: { path } },
      });
      if (!data) {
        if (path === ".") setError(errorMessage(failure, "The files could not be read."));
        else setExpanded((current) => new Set([...current].filter((item) => item !== path)));
        return;
      }
      setError(null);
      setLocation(data.location);
      setFolders((current) => new Map(current).set(path, { entries: data.entries, truncated: data.truncated }));
    },
    [projectId],
  );

  // Read every open folder again when the project or the files may have changed.
  useEffect(() => {
    for (const path of expandedRef.current) void loadFolder(path);
  }, [loadFolder, refreshKey]);

  useEffect(() => {
    const text = query.trim();
    if (!text) {
      setResults(null);
      return;
    }
    let current = true;
    const timer = window.setTimeout(async () => {
      setSearching(true);
      const { data, error: failure } = await api.GET("/api/v1/projects/{project_id}/files/search", {
        params: { path: { project_id: projectId }, query: { query: text, mode } },
      });
      if (!current) return;
      setSearching(false);
      if (data) setResults(data);
      else {
        setResults(null);
        setError(errorMessage(failure, "The search failed."));
      }
    }, 300);
    return () => {
      current = false;
      window.clearTimeout(timer);
    };
  }, [query, mode, projectId, refreshKey]);

  function toggle(path: string) {
    if (expanded.has(path)) {
      setExpanded((current) => new Set([...current].filter((item) => item !== path)));
      return;
    }
    setExpanded((current) => new Set(current).add(path));
    if (!folders.has(path)) void loadFolder(path);
  }

  function rows(path: string, depth: number): ReactNode[] {
    const folder = folders.get(path);
    if (!folder) return [<p key={`${path}-loading`} className="faint small ft-note" style={{ paddingLeft: 16 + depth * 14 }}>Loading…</p>];
    if (folder.entries.length === 0 && path === ".") return [<p key="empty" className="faint small pp-pad">The project has no files yet.</p>];
    const shown: ReactNode[] = [];
    for (const entry of folder.entries) {
      const open = entry.is_dir && expanded.has(entry.path);
      shown.push(
        <button
          key={entry.path}
          type="button"
          className="ft-row"
          style={{ paddingLeft: 10 + depth * 14 }}
          aria-expanded={entry.is_dir ? open : undefined}
          aria-current={selected === entry.path}
          disabled={!entry.is_dir && !onOpen}
          onClick={() => (entry.is_dir ? toggle(entry.path) : onOpen?.(entry.path))}
          title={entry.path}
        >
          <span className={`ft-chevron${open ? " ft-open" : ""}`}>{entry.is_dir && <ChevronIcon size={12} />}</span>
          <span className="ft-icon">{entry.is_dir ? <FolderIcon size={14} /> : <FileIcon size={14} />}</span>
          <span className="ft-name">{entry.name}</span>
          <EntryMeta entry={entry} />
        </button>,
      );
      if (open) shown.push(...rows(entry.path, depth + 1));
    }
    if (folder.truncated)
      shown.push(
        <p key={`${path}-more`} className="faint small ft-note" style={{ paddingLeft: 16 + depth * 14 }}>
          More files than are shown; search to find one.
        </p>,
      );
    return shown;
  }

  return (
    <div className="ft">
      <div className="ft-bar">
        <input
          type="search"
          aria-label="Search the project's files"
          placeholder={mode === "name" ? "Find a file by name" : "Find text in files"}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <Segmented
          label="Search by"
          value={mode}
          onChange={setMode}
          options={[
            { value: "name", label: "Name" },
            { value: "content", label: "Content" },
          ]}
        />
      </div>
      {location && (
        <p className="faint small ft-location">
          {location.kind === "container" ? "In YAAP's container" : `On ${location.label}`}
          {" · "}
          <button type="button" className="link-button" onClick={() => expanded.forEach((path) => void loadFolder(path))}>
            Refresh
          </button>
        </p>
      )}
      {error && <p className="notice pp-pad">{error}</p>}
      <div className="ft-list">
        {results ? (
          <>
            {searching && <p className="faint small pp-pad">Searching…</p>}
            {results.mode === "name" &&
              results.entries.map((entry) => (
                <button
                  key={entry.path}
                  type="button"
                  className="ft-row"
                  aria-current={selected === entry.path}
                  disabled={!entry.is_dir && !onOpen}
                  onClick={() => {
                    if (!entry.is_dir) onOpen?.(entry.path);
                    else {
                      setQuery("");
                      const parts = entry.path.split("/");
                      const opened = parts.map((_, index) => parts.slice(0, index + 1).join("/"));
                      setExpanded((current) => new Set([...current, ...opened]));
                      for (const path of opened) void loadFolder(path);
                    }
                  }}
                  title={entry.path}
                >
                  <span className="ft-icon">{entry.is_dir ? <FolderIcon size={14} /> : <FileIcon size={14} />}</span>
                  <span className="ft-name mono">{entry.path}</span>
                  <EntryMeta entry={entry} />
                </button>
              ))}
            {results.mode === "content" &&
              results.matches.map((match) => (
                <button
                  key={`${match.path}:${match.line}`}
                  type="button"
                  className="ft-row ft-match"
                  disabled={!onOpen}
                  onClick={() => onOpen?.(match.path, match.line)}
                  title={`${match.path}, line ${match.line}`}
                >
                  <span className="ft-name mono">
                    {match.path}:{match.line}
                  </span>
                  <span className="ft-line mono faint">{match.text}</span>
                </button>
              ))}
            {!searching && results.entries.length === 0 && results.matches.length === 0 && <p className="faint small pp-pad">Nothing found.</p>}
            {results.truncated && <p className="faint small pp-pad">Only the first results are shown; search more precisely.</p>}
          </>
        ) : (
          !error && rows(".", 0)
        )}
      </div>
    </div>
  );
}

/**
 * The Files tab: the tree, and a file opened in the editor in its place. The tree stays mounted, so
 * going back keeps its open folders. Give it `key={projectId}` so another project starts fresh.
 */
export default function FilesTab({
  projectId,
  refreshKey,
  onOpenDocument,
}: {
  projectId: number;
  refreshKey?: unknown;
  /** Open a Markdown file as the conversation's shared document; absent without a conversation. */
  onOpenDocument?: (path: string) => void;
}) {
  const [open, setOpen] = useState<{ path: string; line?: number } | null>(null);
  const [saved, setSaved] = useState(0);
  return (
    <>
      <div className="ft-pane" hidden={open !== null}>
        <FileTree
          projectId={projectId}
          refreshKey={`${String(refreshKey)}-${saved}`}
          selected={open?.path ?? null}
          onOpen={(path, line) => setOpen({ path, line })}
        />
      </div>
      {open && (
        <FileEditor
          key={`${open.path}:${open.line ?? ""}`}
          projectId={projectId}
          path={open.path}
          initialLine={open.line}
          onClose={() => setOpen(null)}
          onSaved={() => setSaved((count) => count + 1)}
          onOpenAsDocument={
            onOpenDocument &&
            ((path) => {
              onOpenDocument(path);
              setOpen(null);
            })
          }
        />
      )}
    </>
  );
}
