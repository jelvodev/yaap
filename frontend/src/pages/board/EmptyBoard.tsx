import { useState } from "react";

import { useNewConversation } from "./useNewConversation";

/** The slim bar above the columns of a board without cards: start a conversation as before, or note the first idea. */
export default function EmptyBoard({
  projectId,
  onScope,
  onAddIdea,
}: {
  projectId: number;
  onScope: (projectId: number | null) => void;
  onAddIdea: (title: string) => Promise<void>;
}) {
  const [text, setText] = useState("");
  // The typed text cannot be carried into the new conversation yet; Start opens it as the header's button does.
  const startConversation = useNewConversation(projectId, onScope);

  async function addIdea() {
    const title = text.trim();
    if (!title) return;
    await onAddIdea(title);
    setText("");
  }

  return (
    <div className="board-empty-bar">
      <input
        value={text}
        placeholder="Start a conversation, or note an idea…"
        aria-label="Start a conversation, or note an idea"
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => event.key === "Enter" && startConversation()}
      />
      <button type="button" className="button button-primary board-small" onClick={startConversation}>
        Start
      </button>
      <button
        type="button"
        className="button button-ghost board-small"
        disabled={!text.trim()}
        onClick={() => void addIdea()}
      >
        Add idea
      </button>
    </div>
  );
}
