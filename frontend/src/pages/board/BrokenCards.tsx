import type { BoardCard } from "./boardApi";

/** Card files the board could not read; each opens the card, whose detail shows the raw text. */
export default function BrokenCards({ cards, onOpen }: { cards: BoardCard[]; onOpen: (card: BoardCard) => void }) {
  if (cards.length === 0) return null;
  return (
    <div className="board-broken" role="alert">
      <strong>
        {cards.length} card {cards.length === 1 ? "file cannot" : "files cannot"} be read
      </strong>
      <ul>
        {cards.map((card) => (
          <li key={card.id}>
            <button type="button" className="board-broken-row" onClick={() => onOpen(card)}>
              <span className="mono">#{card.id}</span> · <span className="mono">{card.path}</span> ·{" "}
              <span>{card.error ?? "Unreadable"}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
