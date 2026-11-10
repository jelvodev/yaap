/** A mark the API puts in place of a hidden character, such as ⟦U+200B ZERO WIDTH SPACE⟧. */
const MARK = /(⟦[^⟧]*⟧)/;

/**
 * Text from the API in which each hidden character is already replaced by a mark (the detection is in the backend);
 * the marks are drawn so they stand out from the text around them.
 */
export default function MarkedText({ text }: { text: string }) {
  return (
    <>
      {text.split(MARK).map((part, index) =>
        index % 2 === 1 ? (
          <mark key={index} className="hidden-mark">
            {part}
          </mark>
        ) : (
          part
        ),
      )}
    </>
  );
}

/** Warnings about a review, as a list; nothing when there are none. */
export function ReviewWarnings({ warnings }: { warnings: string[] }) {
  if (warnings.length === 0) return null;
  return (
    <ul className="skill-warnings">
      {warnings.map((warning) => (
        <li key={warning}>{warning}</li>
      ))}
    </ul>
  );
}
