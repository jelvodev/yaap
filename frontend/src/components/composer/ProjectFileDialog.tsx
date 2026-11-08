import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { rawFileUrl } from "../../api/files";
import { FileTree } from "../project/FilesTab";
import "../project/project-panel.css";

/** A project file as a file to attach, read from the project as it is now. */
async function projectFile(projectId: number, path: string): Promise<File> {
  const response = await fetch(rawFileUrl(projectId, path));
  if (!response.ok) throw new Error(`${path} could not be read.`);
  const blob = await response.blob();
  return new File([blob], path.split("/").pop() || path, { type: blob.type });
}

/**
 * Attach a file from the project (plan-chat-interface 2.2): the project's file tree with its search, as in the Files
 * panel; a click on a file attaches a copy of it, as a file from the computer would be.
 */
export default function ProjectFileDialog({
  projectId,
  onAttach,
  onClose,
}: {
  projectId: number;
  onAttach: (file: File) => void;
  onClose: () => void;
}) {
  const [reading, setReading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function attach(path: string) {
    setReading(path);
    setError(null);
    try {
      onAttach(await projectFile(projectId, path));
      onClose();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : `${path} could not be read.`);
    } finally {
      setReading(null);
    }
  }

  return createPortal(
    <div className="palette-backdrop" onMouseDown={onClose}>
      <div
        className="palette import-dialog project-file-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Attach from the project"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <h2>Attach from the project</h2>
        <p className="faint small">Click a file to attach a copy of it to the message.</p>
        <div className="project-file-tree">
          <FileTree projectId={projectId} onOpen={(path) => void attach(path)} selected={reading} />
        </div>
        {reading && <p className="faint small">Adding {reading}…</p>}
        {error && <p className="form-error small">{error}</p>}
        <div className="import-row import-actions">
          <button type="button" className="button button-ghost" onClick={onClose}>
            Cancel
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
