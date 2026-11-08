import { useCallback, useEffect, useRef, useState } from "react";

import { formatSize } from "../../api/attachments";
import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";

type KnowledgeDocument = components["schemas"]["KnowledgeDocumentOut"];

const STATE_TEXT: Record<KnowledgeDocument["index_state"], string> = {
  waiting: "being indexed",
  indexed: "searchable",
  words: "searchable by words",
};

async function uploadKnowledge(projectId: number, file: File): Promise<KnowledgeDocument> {
  const form = new FormData();
  form.append("file", file, file.name);
  const response = await fetch(`/api/v1/projects/${projectId}/knowledge`, { method: "POST", body: form });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(errorMessage(body, `${file.name} could not be added (HTTP ${response.status}).`));
  return body as KnowledgeDocument;
}

/**
 * Project knowledge: reference documents the agent searches with search_project and reads with
 * read_knowledge. They live in YAAP's data folder, not in the repository.
 */
export default function KnowledgeTab({ projectId }: { projectId: number }) {
  const [documents, setDocuments] = useState<KnowledgeDocument[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<number | null>(null);
  const [notice, setNotice] = useState<{ error: boolean; text: string } | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const [pageUrl, setPageUrl] = useState("");
  const [folder, setFolder] = useState("");
  const [savedFolder, setSavedFolder] = useState("");

  useEffect(() => {
    void api.GET("/api/v1/projects/{project_id}/settings", { params: { path: { project_id: projectId } } }).then(({ data }) => {
      const value = String(data?.find((item) => item.key === "knowledge_folder")?.value ?? "");
      setFolder(value);
      setSavedFolder(value);
    });
  }, [projectId]);

  async function addPage() {
    setBusy(pageUrl);
    setNotice(null);
    const { error } = await api.POST("/api/v1/projects/{project_id}/knowledge/web", {
      params: { path: { project_id: projectId } },
      body: { url: pageUrl.trim() },
    });
    setBusy(null);
    if (error) setNotice({ error: true, text: errorMessage(error) });
    else setPageUrl("");
    await load();
  }

  async function refresh(document: KnowledgeDocument) {
    setBusy(document.name);
    const { error } = await api.POST("/api/v1/projects/{project_id}/knowledge/{document_id}/refresh", {
      params: { path: { project_id: projectId, document_id: document.id } },
    });
    setBusy(null);
    setNotice(error ? { error: true, text: errorMessage(error) } : { error: false, text: `${document.name} fetched again.` });
    await load();
  }

  async function saveFolder() {
    const value = folder.trim();
    const { error } = value
      ? await api.PUT("/api/v1/projects/{project_id}/settings/{key}", {
          params: { path: { project_id: projectId, key: "knowledge_folder" } },
          body: { value },
        })
      : await api.DELETE("/api/v1/projects/{project_id}/settings/{key}", {
          params: { path: { project_id: projectId, key: "knowledge_folder" } },
        });
    if (error) {
      setNotice({ error: true, text: errorMessage(error) });
      return;
    }
    setSavedFolder(value);
    await syncFolder();
  }

  async function syncFolder() {
    setBusy("the knowledge folder");
    const { data, error } = await api.POST("/api/v1/projects/{project_id}/knowledge/sync", {
      params: { path: { project_id: projectId } },
    });
    setBusy(null);
    setNotice(
      error
        ? { error: true, text: errorMessage(error) }
        : { error: false, text: data?.changed ? `${data.changed} file${data.changed === 1 ? "" : "s"} read from the folder.` : "The folder is up to date." },
    );
    await load();
  }

  const load = useCallback(async () => {
    const { data, error } = await api.GET("/api/v1/projects/{project_id}/knowledge", {
      params: { path: { project_id: projectId } },
    });
    if (data) setDocuments(data);
    else setNotice({ error: true, text: errorMessage(error) });
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  // While a document waits for the index, look again now and then.
  useEffect(() => {
    if (!documents?.some((document) => document.index_state === "waiting")) return;
    const timer = window.setTimeout(() => void load(), 4000);
    return () => window.clearTimeout(timer);
  }, [documents, load]);

  async function add(files: FileList | null) {
    if (!files?.length) return;
    setNotice(null);
    const failures: string[] = [];
    for (const file of Array.from(files)) {
      setBusy(file.name);
      try {
        await uploadKnowledge(projectId, file);
      } catch (error) {
        failures.push(error instanceof Error ? error.message : String(error));
      }
    }
    setBusy(null);
    if (input.current) input.current.value = "";
    if (failures.length) setNotice({ error: true, text: failures.join(" ") });
    await load();
  }

  async function remove(document: KnowledgeDocument) {
    setConfirming(null);
    const { error } = await api.DELETE("/api/v1/projects/{project_id}/knowledge/{document_id}", {
      params: { path: { project_id: projectId, document_id: document.id } },
    });
    if (error) setNotice({ error: true, text: errorMessage(error) });
    else setNotice({ error: false, text: `${document.name} removed.` });
    await load();
  }

  return (
    <div className="pp-pad pp-knowledge">
      <p className="faint small">
        Reference material for this project: manuals, specifications, exports. The agent finds what they say with
        search_project and reads them with read_knowledge. PDF, Word, Excel, PowerPoint, text, or a zip of them; up to 50
        MB each. They are kept in YAAP, not in the repository.
      </p>
      <div className="pp-row">
        <button type="button" className="button button-primary" disabled={busy !== null} onClick={() => input.current?.click()}>
          {busy ? `Adding ${busy}…` : "Add documents"}
        </button>
        <input
          ref={input}
          type="file"
          multiple
          hidden
          aria-label="Documents to add"
          onChange={(event) => void add(event.target.files)}
        />
      </div>
      <form
        className="pp-row"
        onSubmit={(event) => {
          event.preventDefault();
          void addPage();
        }}
      >
        <input
          type="url"
          aria-label="Web page address"
          placeholder="https://… a web page to keep as knowledge"
          value={pageUrl}
          onChange={(event) => setPageUrl(event.target.value)}
        />
        <button type="submit" className="button button-ghost" disabled={busy !== null || !/^https?:\/\//.test(pageUrl.trim())}>
          Add web page
        </button>
      </form>
      <div className="pp-row">
        <input
          aria-label="Knowledge folder"
          className="mono"
          placeholder="A project folder, such as docs"
          value={folder}
          onChange={(event) => setFolder(event.target.value)}
        />
        {folder.trim() !== savedFolder ? (
          <button type="button" className="button button-ghost" onClick={() => void saveFolder()}>
            Use folder
          </button>
        ) : (
          savedFolder && (
            <button type="button" className="button button-ghost" disabled={busy !== null} onClick={() => void syncFolder()}>
              Read again
            </button>
          )
        )}
      </div>
      <p className="faint small">
        The folder's Markdown, text and PDF files are knowledge without uploading; changed files are read again at the next
        index pass. Scanned PDFs and images of text are read with OCR.
      </p>
      {notice && <p className={notice.error ? "form-error small" : "muted small"}>{notice.text}</p>}
      {documents === null ? (
        <p className="faint small">Loading…</p>
      ) : documents.length === 0 ? (
        <p className="faint small">No documents yet.</p>
      ) : (
        <ul className="pp-knowledge-list">
          {documents.map((document) => (
            <li key={document.id}>
              <div className="pp-knowledge-name">
                <span className="mono">{document.name}</span>
                <span className="faint small">
                  {document.source_url ? "web page" : document.source_path ? "from the folder" : formatSize(document.size)} ·{" "}
                  {STATE_TEXT[document.index_state]}
                  {document.ocr && " · read with OCR"}
                  {document.partly_indexed && " · only the first part is searchable by meaning"}
                  {document.fetched_at && document.source_url && ` · fetched ${new Date(document.fetched_at).toLocaleString()}`}
                </span>
                {document.source_url && (
                  <a className="faint small" href={document.source_url} target="_blank" rel="noreferrer">
                    {document.source_url}
                  </a>
                )}
              </div>
              {document.source_url && confirming !== document.id && (
                <button type="button" className="button button-ghost" disabled={busy !== null} onClick={() => void refresh(document)}>
                  Refresh
                </button>
              )}
              {confirming === document.id ? (
                <span className="pp-row">
                  <button type="button" className="button button-danger" onClick={() => void remove(document)}>
                    Remove
                  </button>
                  <button type="button" className="button button-ghost" onClick={() => setConfirming(null)}>
                    Keep
                  </button>
                </span>
              ) : (
                <button type="button" className="button button-ghost" onClick={() => setConfirming(document.id)}>
                  Remove…
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
