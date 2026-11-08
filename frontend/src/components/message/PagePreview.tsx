import { useEffect } from "react";

/** The SVG as an image source, or null while it is incomplete or not an SVG. */
export function svgSource(code: string): string | null {
  const trimmed = code.trim();
  if (!trimmed.includes("<svg")) return null;
  const parsed = new DOMParser().parseFromString(trimmed, "image/svg+xml");
  if (parsed.getElementsByTagName("parsererror").length > 0 || parsed.documentElement.nodeName !== "svg") return null;
  // As an <img>, an SVG cannot run scripts or load anything; it is only drawn.
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(trimmed)}`;
}

// What a previewed page may do. Together with the sandbox (no allow-same-origin) the page runs on an opaque origin:
// it cannot read YAAP's pages or storage, and it cannot reach YAAP's API (http://localhost) or submit forms.
const PREVIEW_POLICY =
  "default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval' https:; style-src 'unsafe-inline' https:; " +
  "img-src data: blob: https:; font-src data: https:; media-src data: blob: https:; connect-src https:; " +
  "form-action 'none'; frame-src 'none'; base-uri 'none'";

export function looksLikePage(language: string, code: string): boolean {
  const start = code.trimStart().slice(0, 200).toLowerCase();
  return language === "html" || start.startsWith("<!doctype html") || start.startsWith("<html");
}

/** The page with YAAP's policy as the very first thing the parser meets, ahead of any script in the page. */
function previewDocument(code: string): string {
  const meta = `<meta http-equiv="Content-Security-Policy" content="${PREVIEW_POLICY}">`;
  const doctype = /^\s*<!doctype[^>]*>/i.exec(code);
  if (doctype) return `${doctype[0]}${meta}${code.slice(doctype[0].length)}`;
  return `<!doctype html>${meta}${code}`;
}

/** A page from a reply, drawn in an isolated frame: inline, or full screen with a close bar when `onClose` is given. */
export default function PagePreview({ code, onClose }: { code: string; onClose?: () => void }) {
  useEffect(() => {
    if (!onClose) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);
  const frame = (
    <iframe
      className="page-preview-frame"
      title="Preview of the page from the reply"
      sandbox="allow-scripts allow-modals"
      referrerPolicy="no-referrer"
      srcDoc={previewDocument(code)}
    />
  );
  if (!onClose) return <div className="page-preview">{frame}</div>;
  return (
    <div className="page-preview-overlay" role="dialog" aria-modal="true" aria-label="Page preview">
      <div className="page-preview-overlay-bar">
        <span className="muted small">Preview · runs isolated from YAAP</span>
        <button type="button" className="button" onClick={onClose} autoFocus>
          Close
        </button>
      </div>
      {frame}
    </div>
  );
}
