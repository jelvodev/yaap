import { useEffect } from "react";

import type { Project } from "../../api/client";
import { useBoardLinks } from "../chat/useChatLinks";
import "./board.css";
import BoardColumn, { type ColumnActions, newCard } from "./BoardColumn";
import BoardHeader from "./BoardHeader";
import BoardOff from "./BoardOff";
import BrokenCards from "./BrokenCards";
import CardDetail from "./CardDetail";
import EmptyBoard from "./EmptyBoard";
import ListView from "./ListView";
import type { Board } from "./boardApi";
import { useBoard } from "./useBoard";
import type { BoardHandle } from "./useBoard";
import { useBoardColumns } from "./useBoardColumns";
import { useBoardDnd } from "./useBoardDnd";
import { type BoardPrefs, useBoardPrefs, useNarrow } from "./useBoardPrefs";

interface BoardPageProps {
  projectId: number;
  cardId: number | null;
  projects: Project[];
  onScope: (projectId: number | null) => void;
  onProjectChanged: (project: Project) => void;
}

function Unreadable({ message, retry }: { message: string | null; retry: () => Promise<void> }) {
  return (
    <div className="board-page board-message">
      <p className="board-notice">{message ?? "The board could not be read."}</p>
      <button type="button" className="button" onClick={() => void retry()}>
        Retry
      </button>
    </div>
  );
}

interface LoadedProps {
  handle: BoardHandle;
  board: Board;
  prefs: BoardPrefs;
  projectId: number;
  cardId: number | null;
  project: Project | null;
}

/** A started board with cards: header, columns or list, broken cards, and the open card's detail. */
function LoadedBoard({ handle, board, prefs, projectId, cardId, project }: LoadedProps) {
  const dnd = useBoardDnd(handle);
  const narrow = useNarrow();
  const { openBoard, openCard } = useBoardLinks();
  const columns = useBoardColumns(board, handle.cards, prefs.filter);

  const actions: ColumnActions = {
    projectId,
    selectedId: cardId,
    doneOpen: prefs.doneOpen,
    setDoneOpen: prefs.setDoneOpen,
    onMove: dnd.moveTo,
    onAdd: async (title, column) => {
      const version = column === "idea" && prefs.filter.kind === "version" ? prefs.filter.name : null;
      await handle.addCard(newCard(title, column, version));
    },
    onDragStart: dnd.start,
    onDragEnd: dnd.end,
    onDrop: dnd.drop,
    dragging: dnd.dragging,
  };

  const detail = cardId !== null && (
    <aside className="board-detail">
      {narrow && (
        <button type="button" className="button button-ghost board-back" onClick={() => openBoard(projectId)}>
          ← Board
        </button>
      )}
      <CardDetail
        projectId={projectId}
        cardId={cardId}
        head={board.head}
        project={project}
        versions={board.versions}
        currentVersion={prefs.filter.kind === "version" ? prefs.filter.name : board.current_version}
        onClose={() => openBoard(projectId)}
        onChanged={handle.reload}
      />
    </aside>
  );
  if (narrow && detail) return <div className="board-page">{detail}</div>;

  const message = dnd.notice ?? handle.notice;
  return (
    <div className="board-page">
      <BoardHeader
        board={board}
        projectId={projectId}
        projectName={project?.name ?? "Board"}
        repositoryUrl={project?.repository_url ?? null}
        filter={prefs.filter}
        setFilter={prefs.setFilter}
        view={prefs.view}
        setView={prefs.setView}
        reload={handle.reload}
      />
      {message && (
        <p className="board-notice" role="status">
          <span>{message}</span>
          <button type="button" className="board-notice-close" aria-label="Dismiss" onClick={dnd.dismiss}>
            ×
          </button>
        </p>
      )}
      {handle.error && <p className="form-error small board-error">{handle.error}</p>}
      <div className="board-body">
        <div className="board-scroll">
          {narrow || prefs.view === "list" ? (
            <ListView columns={columns} actions={actions} />
          ) : (
            <div className="board-columns">
              {columns.map((data) => (
                <BoardColumn key={data.column} data={data} actions={actions} />
              ))}
            </div>
          )}
          <BrokenCards
            cards={handle.cards.filter((card) => card.broken)}
            onOpen={(card) => openCard(projectId, card.id)}
          />
        </div>
        {detail}
      </div>
    </div>
  );
}

/** The project's board in place of the conversation. */
export default function BoardPage({ projectId, cardId, projects, onScope, onProjectChanged }: BoardPageProps) {
  const handle = useBoard(projectId);
  const prefs = useBoardPrefs(projectId);
  const project = projects.find((item) => item.id === projectId) ?? null;

  useEffect(() => {
    onScope(projectId);
  }, [onScope, projectId]);

  const board = handle.board;
  if (board === null) {
    if (handle.loading) return <div className="board-page board-message">Loading the board…</div>;
    return <Unreadable message={handle.error} retry={handle.reload} />;
  }
  if (board.state === "off") {
    return (
      <div className="board-page">
        <BoardOff
          projectId={projectId}
          source={board.enabled_source}
          onProjectChanged={onProjectChanged}
          reload={handle.reload}
        />
      </div>
    );
  }
  if (board.state === "unavailable") return <Unreadable message={board.message} retry={handle.reload} />;
  if (board.state === "not_started" || handle.cards.length === 0) {
    return (
      <div className="board-page">
        <EmptyBoard board={handle} projectId={projectId} onScope={onScope} />
      </div>
    );
  }
  return (
    <LoadedBoard handle={handle} board={board} prefs={prefs} projectId={projectId} cardId={cardId} project={project} />
  );
}
