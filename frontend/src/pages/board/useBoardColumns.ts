import { useMemo } from "react";

import type { Board, BoardCard } from "./boardApi";
import type { ColumnData } from "./BoardColumn";
import { COLUMNS, type VersionFilter, filterByVersion, groupByColumn } from "./boardLogic";

/** The columns to show for a version filter; the server's counts only apply to the unfiltered board. */
export function useBoardColumns(board: Board | null, cards: BoardCard[], filter: VersionFilter): ColumnData[] {
  return useMemo(() => {
    const groups = groupByColumn(filterByVersion(cards, filter));
    const server = filter.kind === "all" ? board?.columns : null;
    return COLUMNS.map((column) => ({
      column,
      cards: groups[column],
      count: server?.[column]?.count ?? groups[column].length,
      truncated: server?.[column]?.truncated ?? false,
    }));
  }, [board, cards, filter]);
}
