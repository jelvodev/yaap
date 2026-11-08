const MATCH_START = "\u0002";
const MATCH_END = "\u0003";

/** A search snippet with the matched words highlighted (the server marks them with \u0002 and \u0003). */
export default function Snippet({ text }: { text: string }) {
  const parts: React.ReactNode[] = [];
  let rest = text;
  let key = 0;
  while (rest) {
    const start = rest.indexOf(MATCH_START);
    if (start === -1) {
      parts.push(rest);
      break;
    }
    const end = rest.indexOf(MATCH_END, start);
    parts.push(rest.slice(0, start));
    parts.push(<mark key={key++}>{rest.slice(start + 1, end === -1 ? undefined : end)}</mark>);
    rest = end === -1 ? "" : rest.slice(end + 1);
  }
  return <span className="snippet">{parts}</span>;
}
