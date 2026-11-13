import { Markdown } from "../../components/message/Markdown";
import { useCurrentState } from "./useCurrentState";

interface CurrentStateCardProps {
  projectId: number;
  /** Put a message into the composer, not sent. */
  onContinue: (text: string) => void;
  /** Open the project's memory, where the block is edited. */
  onEdit: () => void;
}

/** What the project's current state says, at the start of a new conversation of the project. */
export default function CurrentStateCard({ projectId, onContinue, onEdit }: CurrentStateCardProps) {
  const { state } = useCurrentState(projectId);
  if (!state || !state.text.trim()) return null;
  return (
    <section className="current-state-card">
      <h3>Where this project stands</h3>
      <div className="current-state-text">
        <Markdown text={state.text} plain />
      </div>
      <div className="current-state-actions">
        <button
          type="button"
          className="button button-primary"
          onClick={() => onContinue("Continue from the current state: take the next step.")}
        >
          Continue from here
        </button>
        <button type="button" className="button button-ghost" onClick={onEdit}>
          Edit
        </button>
      </div>
    </section>
  );
}
