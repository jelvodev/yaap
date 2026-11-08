import { Suspense, lazy, useCallback, useEffect, useState } from "react";

import { formatSize } from "../../api/attachments";
import { api, errorMessage } from "../../api/client";
import { changedAgo, rawFileUrl } from "../../api/files";
import type { components } from "../../api/schema";
import { useConfirm } from "../dialog/ConfirmDialog";

type Content = components["schemas"]["FileContentOut"];

// CodeMirror and its languages load only when a file is opened.
const CodeEditor = lazy(() => import("./CodeEditor"));

type Notice = { tone: "error" | "ok" | "conflict"; text: string } | null;

/**
 * One project file in the editor: text in CodeMirror, saved with the hash of the version that was
 * opened (a save is refused when the file changed since, by the agent or anyone else) after a checkpoint; images and
 * PDFs shown; large files read-only.
 */
export default function FileEditor({
  projectId,
  path,
  initialLine,
  onClose,
  onSaved,
  onOpenAsDocument,
}: {
  projectId: number;
  path: string;
  initialLine?: number;
  /** Back to the list; asks first when there are unsaved changes. */
  onClose: () => void;
  onSaved?: () => void;
  /** Open a Markdown file as the conversation's shared document beside the chat. */
  onOpenAsDocument?: (path: string) => void;
}) {
  const confirm = useConfirm();
  const [file, setFile] = useState<Content | null>(null);
  const [text, setText] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [saving, setSaving] = useState(false);
  const dirty = file?.text != null && text !== file.text;

  const load = useCallback(async () => {
    setLoadError(null);
    const { data, error } = await api.GET("/api/v1/projects/{project_id}/files/content", {
      params: { path: { project_id: projectId }, query: { path } },
    });
    if (!data) {
      setLoadError(errorMessage(error, "The file could not be opened."));
      return;
    }
    setFile(data);
    setText(data.text ?? "");
  }, [projectId, path]);

  useEffect(() => {
    void load();
  }, [load]);

  // Unsaved changes are not lost by closing the tab without a warning.
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const save = useCallback(async () => {
    if (!file || file.read_only || !dirty || saving) return;
    setSaving(true);
    setNotice(null);
    const { data, error, response } = await api.PUT("/api/v1/projects/{project_id}/files/content", {
      params: { path: { project_id: projectId } },
      body: { path: file.path, text, base_hash: file.hash },
    });
    setSaving(false);
    if (!data) {
      setNotice({ tone: response.status === 409 ? "conflict" : "error", text: errorMessage(error, "The file could not be saved.") });
      return;
    }
    setFile({ ...file, text, hash: data.hash, size: data.size, modified: data.modified });
    setNotice({ tone: "ok", text: data.checkpoint_id ? "Saved. A checkpoint holds the version before." : "Saved." });
    onSaved?.();
  }, [file, dirty, saving, projectId, text, onSaved]);

  async function close() {
    if (dirty && !(await confirm({ message: "Close without saving your changes?", confirmLabel: "Close without saving", tone: "danger" }))) return;
    onClose();
  }

  async function reload() {
    if (dirty && !(await confirm({ message: "Reload the file and drop your changes?", confirmLabel: "Reload", tone: "danger" }))) return;
    setNotice(null);
    void load();
  }

  return (
    <div className="fe">
      <div className="fe-head">
        <button type="button" className="link-button" onClick={close}>
          ← Files
        </button>
        <span className="fe-path mono" title={path}>
          {path}
          {dirty && <span className="fe-dirty" aria-label="Unsaved changes"> ●</span>}
        </span>
        {file && (
          <span className="faint small fe-facts">
            {formatSize(file.size)} · {changedAgo(file.modified)}
          </span>
        )}
        {file?.kind === "text" && onOpenAsDocument && /\.(md|markdown)$/i.test(file.path) && (
          <button
            type="button"
            className="button fe-document"
            disabled={dirty}
            title={dirty ? "Save your changes first" : "Open beside the chat, to write it together with YAAP"}
            onClick={() => onOpenAsDocument(file.path)}
          >
            Open beside the chat
          </button>
        )}
        {file?.kind === "text" && !file.read_only && (
          <button type="button" className="button button-primary fe-save" disabled={!dirty || saving} onClick={() => void save()} title="Save (Ctrl+S)">
            {saving ? "Saving…" : "Save"}
          </button>
        )}
      </div>
      {file && (
        <p className="faint small fe-where">
          {file.read_only
            ? (file.note ?? "Read-only.")
            : file.location.checkpoints
              ? "Saving takes a checkpoint first, so it can be rolled back in Checkpoints."
              : `On ${file.location.label}: a save cannot be rolled back in YAAP.`}
        </p>
      )}
      {notice && (
        <div className={notice.tone === "ok" ? "fe-notice muted small" : "fe-notice notice"}>
          <span>{notice.text}</span>
          {notice.tone === "conflict" && (
            <button type="button" className="link-button" onClick={reload}>
              Reload the file
            </button>
          )}
        </div>
      )}
      {loadError && <p className="notice pp-pad">{loadError}</p>}
      <div className="fe-body">
        {!file && !loadError && <p className="faint small pp-pad">Opening…</p>}
        {file?.kind === "text" && (
          <Suspense fallback={<p className="faint small pp-pad">Loading the editor…</p>}>
            <CodeEditor
              key={`${file.path}`}
              value={text}
              filename={file.name}
              readOnly={file.read_only}
              onChange={setText}
              onSave={() => void save()}
              initialLine={initialLine}
              ariaLabel={`Contents of ${file.path}`}
            />
          </Suspense>
        )}
        {file?.kind === "image" && (
          <div className="fe-media">
            <img src={`${rawFileUrl(projectId, file.path)}&v=${file.hash ?? ""}`} alt={file.name} />
          </div>
        )}
        {file?.kind === "pdf" && <iframe className="fe-pdf" src={rawFileUrl(projectId, file.path)} title={file.name} />}
        {(file?.kind === "binary" || file?.kind === "too_large") && (
          <p className="faint small pp-pad">
            {file.note}{" "}
            {file.kind === "binary" && (
              <a href={rawFileUrl(projectId, file.path)} download={file.name}>
                Download
              </a>
            )}
          </p>
        )}
      </div>
    </div>
  );
}
