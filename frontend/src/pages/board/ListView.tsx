import { type ColumnActions, type ColumnData, AddCard, isFolded } from "./BoardColumn";
import { COLUMN_LABELS } from "./boardLogic";
import CardTile from "./CardTile";

/** The narrow layout: one section per column, the same tiles one under the other. */
export default function ListView({ columns, actions }: { columns: ColumnData[]; actions: ColumnActions }) {
  return (
    <div className="board-list">
      {columns.map((data) => {
        const { column } = data;
        const keep = column === "inbox" || column === "idea";
        if (data.cards.length === 0 && !keep && data.count === 0) return null;
        if (isFolded(column, actions.doneOpen)) {
          return (
            <button key={column} type="button" className="board-list-folded" onClick={() => actions.setDoneOpen(true)}>
              {COLUMN_LABELS[column]} <span className="board-count">{data.count}</span>
            </button>
          );
        }
        return (
          <section key={column} className="board-list-section" aria-label={COLUMN_LABELS[column]}>
            <h3 className="board-list-head">
              {COLUMN_LABELS[column]} <span className="board-count">{data.count}</span>
            </h3>
            {data.cards.map((card) => (
              <CardTile
                key={card.id}
                card={card}
                projectId={actions.projectId}
                selected={card.id === actions.selectedId}
                onMove={actions.onMove}
              />
            ))}
            {keep && <AddCard column={column} onAdd={actions.onAdd} />}
          </section>
        );
      })}
    </div>
  );
}
