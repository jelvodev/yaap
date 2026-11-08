/** A unified diff with added, removed and hunk lines coloured. */
export default function DiffView({ diff }: { diff: string }) {
  if (!diff.trim()) return <p className="faint small">No changes.</p>;
  return (
    <pre className="diff">
      {diff.split("\n").map((line, index) => {
        const kind =
          line.startsWith("+++") || line.startsWith("---")
            ? "diff-file"
            : line.startsWith("+")
              ? "diff-add"
              : line.startsWith("-")
                ? "diff-remove"
                : line.startsWith("@@")
                  ? "diff-hunk"
                  : line.startsWith("diff ")
                    ? "diff-header"
                    : "";
        return (
          <span key={index} className={kind}>
            {line}
            {"\n"}
          </span>
        );
      })}
    </pre>
  );
}
