import { type ReactNode, isValidElement, useContext, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { highlightPlugin } from "../highlight";
import { StreamingContext } from "./contexts";
import PagePreview, { looksLikePage, svgSource } from "./PagePreview";

function nodeText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join("");
  if (isValidElement<{ children?: ReactNode }>(node)) return nodeText(node.props.children);
  return "";
}

function codeText(children: ReactNode): string {
  return nodeText(children);
}

function download(name: string, text: string, type: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function CodeBlock({ children }: { children?: ReactNode }) {
  const preRef = useRef<HTMLPreElement | null>(null);
  const streaming = useContext(StreamingContext);
  const [copied, setCopied] = useState(false);
  const [showCode, setShowCode] = useState(false);
  const [fullScreen, setFullScreen] = useState(false);
  const className = isValidElement<{ className?: string }>(children) ? (children.props.className ?? "") : "";
  const language = /language-(\S+)/.exec(className)?.[1] ?? "text";
  const code = codeText(children);
  const page = looksLikePage(language, code) && (!streaming || /<\/html>\s*$/i.test(code)) ? code : null;
  const image = !page && ["svg", "xml", "html", "text"].includes(language) ? svgSource(code) : null;
  const visual = page !== null || image !== null;

  async function copy() {
    try {
      await navigator.clipboard.writeText(code || (preRef.current?.innerText ?? ""));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="code-block">
      <div className="code-block-bar">
        <span>{image ? "svg" : language}</span>
        <span className="code-block-actions">
          {visual && (
            <button type="button" onClick={() => setShowCode((value) => !value)}>
              {showCode ? (page ? "Show page" : "Show image") : "Show code"}
            </button>
          )}
          {page && (
            <button type="button" onClick={() => setFullScreen(true)}>
              Full screen
            </button>
          )}
          {visual && (
            <button
              type="button"
              onClick={() =>
                page ? download("page.html", code, "text/html") : download("image.svg", code.trim(), "image/svg+xml")
              }
            >
              Download
            </button>
          )}
          <button type="button" onClick={() => void copy()}>
            {copied ? "Copied" : "Copy"}
          </button>
        </span>
      </div>
      {page && !showCode ? (
        <PagePreview code={page} />
      ) : image && !showCode ? (
        <div className="svg-preview">
          <img src={image} alt="SVG image from the reply" />
        </div>
      ) : (
        <pre ref={preRef}>{children}</pre>
      )}
      {page && fullScreen && <PagePreview code={page} onClose={() => setFullScreen(false)} />}
    </div>
  );
}

/** Markdown of a reply or document, with highlighted code blocks that preview pages and SVG images. */
export function Markdown({ text }: { text: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[highlightPlugin]}
      components={{
        a: (props) => <a {...props} target="_blank" rel="noreferrer" />,
        pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
      }}
    >
      {text}
    </ReactMarkdown>
  );
}
