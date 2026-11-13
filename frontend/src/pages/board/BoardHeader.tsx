import { useState } from "react";

import type { Board, BoardVersion } from "./boardApi";
import { type VersionFilter, budgetColour, versionProgress } from "./boardLogic";
import type { BoardView } from "./useBoardPrefs";
import PullBoard from "./PullBoard";
import PushBoard from "./PushBoard";
import VersionDialog from "./VersionDialog";

interface HeaderProps {
  board: Board;
  projectId: number;
  projectName: string;
  repositoryUrl: string | null;
  filter: VersionFilter;
  setFilter: (filter: VersionFilter) => void;
  view: BoardView;
  setView: (view: BoardView) => void;
  reload: () => Promise<void>;
  /** Before the board started: open the choice of where it is kept. */
  onChooseStorage?: () => void;
  /** Open a new conversation in this project. */
  onNewConversation: () => void;
}

/** The version the meters describe: the filtered one, else the board's current version. */
export function meterVersion(board: Board, filter: VersionFilter): BoardVersion | null {
  const name = filter.kind === "version" ? filter.name : board.current_version;
  return board.versions.find((version) => version.name === name) ?? null;
}

function money(value: number): string {
  return `$${value.toFixed(2)}`;
}

function Meters({ version }: { version: BoardVersion }) {
  const progress = versionProgress(version.done, version.total);
  const level = budgetColour(version.cost_usd, version.budget_usd);
  const spent = money(version.cost_usd);
  return (
    <div className="board-meters">
      <div className="board-meter">
        <span>
          {version.name} · {progress.done} of {progress.total} done
        </span>
        <div
          className="board-bar"
          role="progressbar"
          aria-valuenow={progress.percent}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <span style={{ width: `${progress.percent}%` }} />
        </div>
      </div>
      <div className={`board-meter board-budget-${level}`}>
        {level === "none" || version.budget_usd === null ? (
          <span>No budget</span>
        ) : (
          <>
            <span>
              Budget {version.name} {spent} of {money(version.budget_usd)}
            </span>
            <div className="board-bar">
              <span style={{ width: `${Math.min(100, (version.cost_usd / (version.budget_usd || 1)) * 100)}%` }} />
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function Segment({ active, label, onClick }: { active: boolean; label: string; onClick: () => void }) {
  return (
    <button type="button" className={active ? "active" : ""} aria-pressed={active} onClick={onClick}>
      {label}
    </button>
  );
}

/** Project name, version filter, progress and budget of the shown version, view toggle and Push board. */
export default function BoardHeader(props: HeaderProps) {
  const { board, filter, setFilter, view, setView } = props;
  const [dialog, setDialog] = useState<{ version: BoardVersion | null } | null>(null);
  const shown = meterVersion(board, filter);
  return (
    <header className="board-header">
      <div className="board-header-row">
        <h2 className="board-title">{props.projectName}</h2>
        <div className="board-segments" role="group" aria-label="Version filter">
          {board.versions.map((version) => (
            <Segment
              key={version.name}
              label={version.name}
              active={filter.kind === "version" && filter.name === version.name}
              onClick={() => setFilter({ kind: "version", name: version.name })}
            />
          ))}
          <Segment
            label="Not scheduled"
            active={filter.kind === "unscheduled"}
            onClick={() => setFilter({ kind: "unscheduled" })}
          />
          <Segment label="All" active={filter.kind === "all"} onClick={() => setFilter({ kind: "all" })} />
        </div>
        <div className="board-header-end">
          <button type="button" className="button button-primary board-small" onClick={props.onNewConversation}>
            New conversation
          </button>
          <div className="board-segments" role="group" aria-label="View">
            <Segment label="Board" active={view === "board"} onClick={() => setView("board")} />
            <Segment label="List" active={view === "list"} onClick={() => setView("list")} />
          </div>
          {board.storage === "repository" && <PullBoard projectId={props.projectId} reload={props.reload} />}
          {board.push?.allowed && (
            <PushBoard
              projectId={props.projectId}
              repositoryUrl={props.repositoryUrl}
              pushed={board.push.pushed}
              storage={board.storage}
              location={board.location}
              reload={props.reload}
            />
          )}
        </div>
      </div>
      <div className="board-header-row">
        {shown && <Meters version={shown} />}
        <div className="board-header-end">
          {props.onChooseStorage && (
            <button type="button" className="link-button small" onClick={props.onChooseStorage}>
              Where the board is kept
            </button>
          )}
          {shown && (
            <button
              type="button"
              className="button button-ghost board-small"
              onClick={() => setDialog({ version: shown })}
            >
              Edit version
            </button>
          )}
          {board.state === "ready" && (
            <button
              type="button"
              className="button button-ghost board-small"
              onClick={() => setDialog({ version: null })}
            >
              + Version
            </button>
          )}
        </div>
      </div>
      {dialog && (
        <VersionDialog
          projectId={props.projectId}
          version={dialog.version}
          onClose={() => setDialog(null)}
          onSaved={props.reload}
        />
      )}
    </header>
  );
}
