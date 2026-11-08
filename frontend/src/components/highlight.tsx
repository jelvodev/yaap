import dockerfile from "highlight.js/lib/languages/dockerfile";
import powershell from "highlight.js/lib/languages/powershell";
import { common } from "lowlight";
import type { ReactNode } from "react";
import rehypeHighlight from "rehype-highlight";

/** Syntax colours for code blocks: highlight.js's common languages plus the ones used most around YAAP. */
export const highlightPlugin: [typeof rehypeHighlight, Parameters<typeof rehypeHighlight>[0]] = [
  rehypeHighlight,
  // Only blocks with a language tag are coloured; guessing colours prose and logs wrongly.
  { detect: false, languages: { ...common, dockerfile, powershell } },
];

const JSON_TOKEN = /("(?:\\.|[^"\\])*")(\s*:)?|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|\b(true|false|null)\b|([{}[\],:])/g;

/**
 * JSON with the same colour classes as highlighted code blocks: keys, strings, numbers, literals, punctuation.
 * Text that is not JSON is returned as it is.
 */
export function JsonText({ text }: { text: string }): ReactNode {
  try {
    JSON.parse(text);
  } catch {
    return text;
  }
  const parts: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(JSON_TOKEN)) {
    const index = match.index ?? 0;
    if (index > last) parts.push(text.slice(last, index));
    const [whole, string, colon, number, literal, punctuation] = match;
    if (string !== undefined) {
      parts.push(
        <span key={index} className={colon ? "hljs-attr" : "hljs-string"}>
          {string}
        </span>,
      );
      if (colon) parts.push(<span key={`${index}:`} className="hljs-punctuation">{colon}</span>);
    } else if (number !== undefined) {
      parts.push(<span key={index} className="hljs-number">{number}</span>);
    } else if (literal !== undefined) {
      parts.push(<span key={index} className="hljs-literal">{literal}</span>);
    } else if (punctuation !== undefined) {
      parts.push(<span key={index} className="hljs-punctuation">{punctuation}</span>);
    } else {
      parts.push(whole);
    }
    last = index + whole.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}
