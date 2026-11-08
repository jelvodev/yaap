import type { PreviewPanelState } from "../../pages/chat/usePreviewPanel";
import { CloseIcon } from "../icons";
import "./preview-panel.css";

interface PreviewPanelProps {
  preview: PreviewPanelState;
  /** The open conversation: its browser takes the screenshot, and the image is kept with it. */
  conversationId: number | null;
}

/**
 * The preview beside the chat: the app of a process the agent started, on the preview's own address,
 * in a frame. The frame is sandboxed without top navigation; the preview's origin can reach nothing of YAAP.
 */
export default function PreviewPanel({ preview, conversationId }: PreviewPanelProps) {
  const chosen = preview.processes.find((item) => item.id === preview.processId);
  return (
    <aside className="preview-panel" aria-label="Preview">
      <div className="pv-head">
        <h2>Preview</h2>
        {preview.processes.length > 0 && (
          <select
            aria-label="Process"
            value={preview.processId ?? ""}
            onChange={(event) => preview.choose(Number(event.target.value))}
          >
            {preview.processes.map((item) => (
              <option key={item.id} value={item.id}>
                Port {item.port} · {item.command.slice(0, 40)}
              </option>
            ))}
          </select>
        )}
        <button type="button" className="button" disabled={!chosen} onClick={preview.reload}>
          Reload
        </button>
        <button
          type="button"
          className="button"
          disabled={!chosen || conversationId === null || preview.takingShot}
          title="A screenshot through the agent's browser"
          onClick={preview.screenshot}
        >
          {preview.takingShot ? "Taking…" : "Screenshot"}
        </button>
        <button type="button" className="icon-button pv-close" aria-label="Close the preview" onClick={preview.close}>
          <CloseIcon size={15} />
        </button>
      </div>
      {preview.error && <p className="form-error pv-pad" role="alert">{preview.error}</p>}
      {!chosen ? (
        <p className="faint small pv-pad">
          No process with a port runs in this project. Ask the agent to start the dev server with start_process and its
          port.
        </p>
      ) : (
        preview.src && (
          <iframe
            key={preview.frameKey}
            className="pv-frame"
            title={`Preview of port ${chosen.port}`}
            src={preview.src}
            sandbox="allow-scripts allow-same-origin allow-forms allow-modals allow-popups allow-downloads"
            referrerPolicy="no-referrer"
          />
        )
      )}
      {preview.shot !== null && conversationId !== null && (
        <figure className="pv-shot">
          <img
            src={`/api/v1/conversations/${conversationId}/browser/shots/${preview.shot}`}
            alt="Screenshot of the preview through the agent's browser"
          />
          <figcaption className="faint small">Screenshot {preview.shot}, through the agent's browser</figcaption>
        </figure>
      )}
    </aside>
  );
}
