import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../../api/client";
import { boardProblem } from "../../pages/board/boardApi";
import { cardPath } from "../../pages/chat/useChatLinks";

interface Props {
  projectId: number;
  reviewId: number;
  /** The finding's place in the review's list of findings. */
  finding: number;
}

/** *Make a card* on a review's finding that the change does not fix: a card in the board's Inbox. */
export default function MakeCardFromFinding({
  projectId,
  reviewId,
  finding,
}: Props) {
  const [made, setMade] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function make(): Promise<void> {
    setBusy(true);
    setError(null);
    const { data, error: failed } = await api.POST(
      "/api/v1/projects/{project_id}/board/cards/from-finding",
      {
        params: { path: { project_id: projectId } },
        body: { review_id: reviewId, finding },
      },
    );
    setBusy(false);
    if (data) setMade(data.id);
    else setError(boardProblem(failed, "The card could not be made.").message);
  }

  if (made !== null) {
    return (
      <Link className="small" to={cardPath(projectId, made)}>
        Card #{made} in Inbox
      </Link>
    );
  }
  return (
    <span className="small">
      <button
        type="button"
        className="button button-ghost board-small"
        disabled={busy}
        onClick={() => void make()}
      >
        Make a card
      </button>
      {error && <span className="form-error"> {error}</span>}
    </span>
  );
}
