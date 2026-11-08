import { Suspense, lazy, useCallback, useEffect, useRef, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";
import DiffView from "../DiffView";
import { Markdown } from "../message/Markdown";
import { CloseIcon } from "../icons";
import { Segmented } from "../workbench/Workbench";
import type { Decision, SharedDocument } from "./useConversationDocument";
import "./document-panel.css";
import { useConfirm } from "../dialog/ConfirmDialog";

type Content = components["schemas"]["FileContentOut"];
type View = "changes" | "rendered" | "source";
type Notice = { tone: "error" | "ok" | "conflict"; text: string } | null;

// CodeMirror loads only when the source is shown.
const CodeEditor = lazy(() => import("../editor/CodeEditor"));

/**
 * The shared document beside the chat: one Markdown file of the project, rendered or as source, edited
 * there and saved with the hash check of the Files tab, so a save never overwrites a change it has not
 * seen.
 *
 * Taking turns: while the agent writes the document the panel is read-only and follows each change; then it
 * shows the change as a diff to accept or roll back, and stays read-only until the user chose. Unsaved text of the
 * user is kept aside meanwhile and can be put back afterwards.
 */
export default function DocumentPanel({
  projectId,
  document,
  version,
  writing,
  delivered,
  replyRunning,
  onDecide,
  onSaved,
  onClose,
}: {
  projectId: number;
  document: SharedDocument;
  /** Changes when the document changed elsewhere (the agent, a roll back), so its text is read again. */
  version: number;
  /** The agent is writing the document now. */
  writing: boolean;
  /** Counts the times the agent got the user's saved changes. */
  delivered: number;
  replyRunning: boolean;
  onDecide: (decision: Decision) => Promise<string | null>;
  onSaved: () => void;
  onClose: () => void;
}) {
  const confirm = useConfirm();
  const [file, setFile] = useState<Content | null>(null);
  const [text, setText] = useState("");
  const [draft, setDraft] = useState<string | null>(null);
  const [view, setView] = useState<View>(document.review ? "changes" : "rendered");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [busy, setBusy] = useState<"save" | Decision | null>(null);
  const review = document.review;
  const yaapsTurn = writing || review !== null;
  const readOnly = (file?.read_only ?? false) || yaapsTurn;
  const base = file?.text ?? "";
  const dirty = text !== base;
  const textRef = useRef(text);
  textRef.current = text;
  const baseRef = useRef(base);
  baseRef.current = base;

  const load = useCallback(async () => {
    setLoadError(null);
    // Text the user did not save yet is kept aside, not dropped, when the document changed elsewhere.
    if (textRef.current !== baseRef.current) setDraft(textRef.current);
    if (!document.exists) {
      setFile(null);
      setText("");
      return;
    }
    const { data, error } = await api.GET("/api/v1/projects/{project_id}/files/content", {
      params: { path: { project_id: projectId }, query: { path: document.path } },
    });
    if (!data) {
      setLoadError(errorMessage(error, "The document could not be read."));
      return;
    }
    setFile(data);
    setText(data.text ?? "");
  }, [projectId, document.path, document.exists]);

  useEffect(() => {
    void load();
    // version: the agent or a roll back changed the document.
  }, [load, version]);

  // The agent's change shows as a diff when its turn starts; after the decision the document shows again.
  const reviewing = review !== null;
  useEffect(() => {
    setView((current) => (reviewing ? "changes" : current === "changes" ? "rendered" : current));
  }, [reviewing]);

  const deliveredAtOpen = useRef(delivered);
  useEffect(() => {
    if (delivered !== deliveredAtOpen.current) setNotice({ tone: "ok", text: "YAAP has your saved changes." });
  }, [delivered]);

  useEffect(() => {
    if (!dirty && draft === null) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, draft]);

  const save = useCallback(async () => {
    if (readOnly || !dirty || busy) return;
    setBusy("save");
    setNotice(null);
    const { data, error, response } = await api.PUT("/api/v1/projects/{project_id}/files/content", {
      params: { path: { project_id: projectId } },
      body: { path: document.path, text, base_hash: file?.hash ?? null },
    });
    setBusy(null);
    if (!data) {
      setNotice({ tone: response.status === 409 ? "conflict" : "error", text: errorMessage(error, "The document could not be saved.") });
      return;
    }
    if (file) setFile({ ...file, text, hash: data.hash, size: data.size, modified: data.modified });
    else {
      // A new file: read it as stored, so it counts as saved.
      const { data: created } = await api.GET("/api/v1/projects/{project_id}/files/content", {
        params: { path: { project_id: projectId }, query: { path: document.path } },
      });
      if (created) {
        setFile(created);
        setText(created.text ?? "");
      }
    }
    setNotice({ tone: "ok", text: "Saved. YAAP gets your changes with its next step." });
    onSaved();
  }, [readOnly, dirty, busy, projectId, document.path, text, file, onSaved]);

  async function decide(decision: Decision) {
    setBusy(decision);
    setNotice(null);
    const failure = await onDecide(decision);
    setBusy(null);
    if (failure) setNotice({ tone: "error", text: failure });
    else setNotice({ tone: "ok", text: decision === "accept" ? "Change accepted." : "Rolled back. YAAP learns of it with its next step." });
  }

  function putDraftBack() {
    if (draft === null) return;
    setText(draft);
    setDraft(null);
    setView("source");
  }

  async function close() {
    const ask = { message: "Close the document without saving your changes?", confirmLabel: "Close without saving", tone: "danger" } as const;
    if ((dirty || draft !== null) && !(await confirm(ask))) return;
    onClose();
  }

  async function reload() {
    if (dirty && !(await confirm({ message: "Read the document again? Your changes are kept aside.", confirmLabel: "Read again" }))) return;
    setNotice(null);
    void load();
  }

  const exists = document.exists;
  let status: string;
  if (writing || (review && replyRunning)) status = "YAAP is changing the document; it is read-only until you have looked at the change.";
  else if (review) status = `YAAP ${review.created ? "created" : "changed"} the document. Accept the change or roll it back to edit again.`;
  else if (!exists) status = "Not created yet: write it here or let YAAP write it.";
  else if (file?.read_only) status = file.note ?? "Read-only.";
  else if (view === "rendered") status = "Switch to Source to edit.";
  else status = "Ctrl+S saves; YAAP gets your saved changes with its next step.";

  return (
    <aside className="document-panel" aria-label={`Document ${document.name}`} aria-busy={writing}>
      <div className="dp-head">
        <h2 className="dp-name" title={document.path}>
          {document.name}
          {dirty && <span className="dp-dirty" aria-label="Unsaved changes"> ●</span>}
        </h2>
        <Segmented
          label="Show the document"
          value={view}
          onChange={setView}
          options={[
            ...(review ? [{ value: "changes" as View, label: "Changes" }] : []),
            { value: "rendered", label: "Rendered" },
            { value: "source", label: "Source" },
          ]}
        />
        {!yaapsTurn && !file?.read_only && (
          <button type="button" className="button button-primary" disabled={!dirty || busy !== null} onClick={() => void save()} title="Save (Ctrl+S)">
            {busy === "save" ? "Saving…" : "Save"}
          </button>
        )}
        <button type="button" className="icon-button dp-close" aria-label="Close the document" onClick={close}>
          <CloseIcon size={15} />
        </button>
      </div>
      <div className={`dp-status small${yaapsTurn ? " dp-turn" : " faint"}`} role="status">
        <span>
          <span className="mono">{document.path}</span> · {status}
        </span>
        {review && !writing && !replyRunning && (
          <span className="dp-decide">
            <button type="button" className="button button-primary" disabled={busy !== null} onClick={() => void decide("accept")}>
              {busy === "accept" ? "Accepting…" : "Accept"}
            </button>
            <button
              type="button"
              className="button"
              disabled={busy !== null}
              onClick={() => void decide("rollback")}
              title={review.created ? "Remove the file YAAP created" : "Put the document back as it was before YAAP's change"}
            >
              {busy === "rollback" ? "Rolling back…" : "Roll back"}
            </button>
          </span>
        )}
      </div>
      {draft !== null && (
        <div className="dp-notice notice">
          <span>YAAP changed the document while you had unsaved changes; they are kept aside.</span>
          <button type="button" className="link-button" disabled={yaapsTurn} onClick={putDraftBack} title={yaapsTurn ? "First accept or roll back YAAP's change" : undefined}>
            Put them back
          </button>
          <button type="button" className="link-button" onClick={() => setDraft(null)}>
            Discard them
          </button>
        </div>
      )}
      {notice && (
        <div className={notice.tone === "ok" ? "dp-notice muted small" : "dp-notice notice"}>
          <span>{notice.text}</span>
          {notice.tone === "conflict" && (
            <button type="button" className="link-button" onClick={reload}>
              Read it again
            </button>
          )}
        </div>
      )}
      {loadError && <p className="notice dp-pad">{loadError}</p>}
      <div className="dp-body">
        {view === "changes" && review ? (
          <div className="dp-changes">
            <DiffView diff={review.diff} />
            {review.truncated && <p className="faint small">The change is too long to show whole.</p>}
            {review.checkpoint_id !== null && (
              <p className="faint small">Checkpoint {review.checkpoint_id} holds the version before YAAP's change.</p>
            )}
          </div>
        ) : view === "source" ? (
          <Suspense fallback={<p className="faint small dp-pad">Loading the editor…</p>}>
            <CodeEditor
              value={text}
              filename={document.name}
              readOnly={readOnly}
              onChange={setText}
              onSave={() => void save()}
              ariaLabel={`Source of ${document.path}`}
            />
          </Suspense>
        ) : (
          <div className="dp-rendered assistant-body" onDoubleClick={() => !readOnly && setView("source")}>
            {text.trim() ? <Markdown text={text} /> : <p className="faint">The document is empty.</p>}
          </div>
        )}
      </div>
    </aside>
  );
}
