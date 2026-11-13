import { type ReactNode, useEffect } from "react";

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
import NotStarted from "./NotStarted";
import PullBoard from "./PullBoard";
import type { Board } from "./boardApi";
import { useBoard } from "./useBoard";
import type { BoardHandle } from "./useBoard";
import { useBoardColumns } from "./useBoardColumns";
import { useBoardDnd } from "./useBoardDnd";
import { type BoardStart, useBoardStart } from "./useBoardStart";
import { useNewConversation } from "./useNewConversation";
import { type BoardPrefs, useBoardPrefs, useNarrow } from "./useBoardPrefs";

interface BoardPageProps {
  projectId: number;
  cardId: number | null;
  projects: Project[];
  onScope: (projectId: number | null) => void;
  onProjectChanged: (project: Project) => void;
}

function Unreadable({
  message,
  retry,
  pull,
}: {
  message: string | null;
  retry: () => Promise<void>;
  pull?: ReactNode;
}) {
  return (
    <div className="board-page board-message">
      <p className="board-notice">{message ?? "The board could not be read."}</p>
      <button type="button" className="button" onClick={() => void retry()}>
        Retry
      </button>
      {pull}
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
  onScope: (projectId: number | null) => void;
}

/** Above the columns: the storage choice while it is open, else the start bar while the board has no cards. */
function BoardStartArea({
  handle,
  starter,
  projectId,
  onScope,
}: {
  handle: BoardHandle;
  starter: BoardStart;
  projectId: number;
  onScope: (projectId: number | null) => void;
}) {
  if (starter.choosing) {
    return (
      <div className="board-start-area">
        <NotStarted
          projectId={projectId}
          start={handle.start}
          pendingTitle={starter.pending?.title ?? null}
          onStarted={() => void starter.started()}
          onCancel={starter.cancel}
        />
      </div>
    );
  }
  if (handle.cards.length > 0) return null;
  return (
    <EmptyBoard
      projectId={projectId}
      onScope={onScope}
      onAddIdea={async (title) => {
        await starter.addCard(newCard(title, "idea"));
      }}
    />
  );
}

/** The board, started or not: header, start area, columns or list, broken cards, and the open card's detail. */
function LoadedBoard({ handle, board, prefs, projectId, cardId, project, onScope }: LoadedProps) {
  const starter = useBoardStart(handle);
  const newConversation = useNewConversation(projectId, onScope);
  const ready = board.state === "ready";
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
      await starter.addCard(newCard(title, column, version));
    },
    onDragStart: dnd.start,
    onDragEnd: dnd.end,
    onDrop: dnd.drop,
    dragging: dnd.dragging,
    // Importing writes to the board, so it waits until the board started.
    onImported: ready ? () => void handle.reload() : undefined,
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
        onChooseStorage={ready ? undefined : starter.choose}
        onNewConversation={newConversation}
      />
      <BoardStartArea handle={handle} starter={starter} projectId={projectId} onScope={onScope} />
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
  if (board.state === "unavailable") {
    const pull = board.storage === "repository" ? <PullBoard projectId={projectId} reload={handle.reload} /> : undefined;
    return <Unreadable message={board.message} retry={handle.reload} pull={pull} />;
  }
  return (
    <LoadedBoard
      handle={handle}
      board={board}
      prefs={prefs}
      projectId={projectId}
      cardId={cardId}
      project={project}
      onScope={onScope}
    />
  );
}
