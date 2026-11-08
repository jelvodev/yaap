import { useCallback, useRef, useState } from "react";

import { type FolderFile, filesFromPicker } from "../../api/folders";
import { FileIcon, FolderIcon, LayersIcon, PaperclipIcon } from "../icons";
import { useDismiss } from "../useDismiss";
import ProjectFileDialog from "./ProjectFileDialog";

function ScreenIcon({ size = 16 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="3" y="4" width="18" height="13" rx="2" />
      <path d="M8 21h8M12 17v4" />
    </svg>
  );
}

export interface AttachMenuProps {
  onFiles: (files: File[]) => void;
  /** A folder chosen in the picker; it is packed into one zip before it uploads. */
  onFolder: (name: string, read: () => Promise<FolderFile[]>) => void;
  /** The project to attach a file from; without one the item is left out. */
  projectId?: number | null;
  /** A screenshot of the running preview; left out while no preview runs. */
  previewShot?: () => Promise<File>;
  onError: (message: string | null) => void;
}

/** Attach as a menu (plan-chat-interface 2.2): files, a folder, a file from the project, a screenshot of the preview. */
export default function AttachMenu({ onFiles, onFolder, projectId, previewShot, onError }: AttachMenuProps) {
  const [open, setOpen] = useState(false);
  const [choosing, setChoosing] = useState(false);
  const [shooting, setShooting] = useState(false);
  const box = useRef<HTMLDivElement | null>(null);
  const fileInput = useRef<HTMLInputElement | null>(null);
  const folderInput = useRef<HTMLInputElement | null>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismiss(box, open, close);

  function pick(input: HTMLInputElement | null) {
    setOpen(false);
    input?.click();
  }

  async function screenshot() {
    if (!previewShot) return;
    setOpen(false);
    setShooting(true);
    onError(null);
    try {
      onFiles([await previewShot()]);
    } catch (failure) {
      onError(failure instanceof Error ? failure.message : "The screenshot could not be taken.");
    } finally {
      setShooting(false);
    }
  }

  return (
    <div className="attach-menu" ref={box}>
      <button
        type="button"
        className="attach-button"
        aria-label="Attach"
        aria-haspopup="menu"
        aria-expanded={open}
        title={shooting ? "Taking a screenshot of the preview…" : "Attach files, a folder or a file from the project"}
        disabled={shooting}
        onClick={() => setOpen((value) => !value)}
      >
        <PaperclipIcon size={16} />
      </button>
      {open && (
        <div className="attach-menu-list" role="menu">
          <button type="button" role="menuitem" onClick={() => pick(fileInput.current)}>
            <FileIcon size={14} /> Files…
            <span className="faint small">images, PDF, Office, text, zip</span>
          </button>
          <button type="button" role="menuitem" onClick={() => pick(folderInput.current)}>
            <FolderIcon size={14} /> A folder…
            <span className="faint small">as one zip, without .git and dependencies</span>
          </button>
          {projectId != null && (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                setChoosing(true);
              }}
            >
              <LayersIcon size={14} /> From the project…
            </button>
          )}
          {previewShot && (
            <button type="button" role="menuitem" onClick={() => void screenshot()}>
              <ScreenIcon size={14} /> Screenshot of the preview
            </button>
          )}
        </div>
      )}
      <input
        ref={fileInput}
        type="file"
        multiple
        hidden
        onChange={(event) => {
          const files = Array.from(event.target.files ?? []);
          if (files.length > 0) onFiles(files);
          event.target.value = "";
        }}
      />
      <input
        ref={folderInput}
        type="file"
        hidden
        {...{ webkitdirectory: "" }}
        onChange={(event) => {
          for (const folder of filesFromPicker(event.target.files ?? [])) onFolder(folder.name, async () => folder.files);
          event.target.value = "";
        }}
      />
      {choosing && projectId != null && (
        <ProjectFileDialog projectId={projectId} onAttach={(file) => onFiles([file])} onClose={() => setChoosing(false)} />
      )}
    </div>
  );
}
