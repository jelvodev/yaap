import type { BlueprintItem } from "../../api/client";
import { Badge, EmptyState, Panel } from "../../components/workbench/Workbench";
import { STATE_TONES } from "./ItemDetail";
import { type ItemKind, STATE_LABELS, itemState, kindInfo } from "./model";

export interface MatrixColumn {
  key: string;
  name: string;
  colour?: string;
  current: boolean;
  items: BlueprintItem[] | undefined;
}

/** Items of one kind as rows and the levels as columns: where each item is defined, overridden or switched off. */
export default function MatrixView({
  kind,
  columns,
  onOpen,
}: {
  kind: ItemKind;
  columns: MatrixColumn[];
  onOpen: (path: string) => void;
}) {
  const rows = new Map<string, string>();
  for (const column of columns) {
    for (const item of column.items ?? []) if (item.kind === kind) rows.set(item.path, item.name);
  }
  const ordered = [...rows.entries()].sort((a, b) => a[1].localeCompare(b[1]));

  return (
    <div className="wb-body">
      <Panel title={`${kindInfo(kind).title} by level`} aside={<span>choose a row to open it at the selected level</span>} flush>
        {ordered.length === 0 ? (
          <EmptyState>No {kindInfo(kind).title.toLowerCase()} at any of these levels.</EmptyState>
        ) : (
          <div className="wb-table-scroll">
            <table className="wb-table bp-matrix">
              <thead>
                <tr>
                  <th>{kindInfo(kind).singular}</th>
                  {columns.map((column) => (
                    <th key={column.key} className={column.current ? "bp-matrix-current" : undefined}>
                      {column.colour && <span className="dot" style={{ background: column.colour }} />} {column.name}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {ordered.map(([path, name]) => (
                  <tr key={path} className="bp-matrix-row" onClick={() => onOpen(path)}>
                    <td>
                      <button type="button" className="link-button mono" onClick={() => onOpen(path)}>
                        {name}
                      </button>
                    </td>
                    {columns.map((column) => {
                      const state = itemState(column.items?.find((item) => item.path === path));
                      return (
                        <td key={column.key} className={column.current ? "bp-matrix-current" : undefined}>
                          {column.items === undefined ? (
                            <span className="faint">…</span>
                          ) : state === "absent" || state === "inherits" ? (
                            <span className="faint small">{state === "absent" ? "–" : STATE_LABELS[state]}</span>
                          ) : (
                            <Badge tone={STATE_TONES[state]} colour={state === "defines" ? column.colour : undefined}>
                              {STATE_LABELS[state]}
                            </Badge>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
