import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { newCard } from "./BoardColumn";
import NotStarted from "./NotStarted";
import type { BoardHandle } from "./useBoard";

/** A board without cards: start a conversation as before, or note the first idea. */
export default function EmptyBoard({
  board,
  projectId,
  onScope,
}: {
  board: BoardHandle;
  projectId: number;
  onScope: (projectId: number | null) => void;
}) {
  const navigate = useNavigate();
  const [text, setText] = useState("");
  const [choosing, setChoosing] = useState(false);
  const ready = board.board?.state === "ready";

  function startConversation() {
    // The typed text cannot be carried into the new conversation yet.
    onScope(projectId);
    navigate("/chat");
  }

  async function addIdea() {
    const title = text.trim();
    if (!title) return;
    if (!ready) {
      setChoosing(true);
      return;
    }
    if ((await board.addCard(newCard(title, "idea"))) !== null) setText("");
  }

  async function startedBoard() {
    setChoosing(false);
    if ((await board.addCard(newCard(text.trim(), "idea"))) !== null) setText("");
  }

  return (
    <div className="board-empty">
      <h3>This project has no cards yet</h3>
      <p className="muted">A small project works as before: start a conversation. Note an idea and the board starts.</p>
      {choosing ? (
        <NotStarted start={board.start} onStarted={() => void startedBoard()} onCancel={() => setChoosing(false)} />
      ) : (
        <div className="board-empty-row">
          <input
            value={text}
            placeholder="Start a conversation, or note an idea…"
            aria-label="Start a conversation, or note an idea"
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && startConversation()}
          />
          <button type="button" className="button button-primary" onClick={startConversation}>
            Start
          </button>
          <button type="button" className="button button-ghost" disabled={!text.trim()} onClick={() => void addIdea()}>
            Add idea
          </button>
        </div>
      )}
    </div>
  );
}
