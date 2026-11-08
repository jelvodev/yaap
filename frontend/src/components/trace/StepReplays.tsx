import { useState } from "react";

import { REPLAY_STATUS_LABELS } from "./runLabels";
import ReplayForm from "./ReplayForm";
import type { Replay, ReplayVariant } from "./useReplays";

interface StepReplaysProps {
  stepId: number;
  /** Only a model call of the reply's own run with a recorded snapshot can be replayed. */
  canReplay: boolean;
  replays: Replay[];
  error: string | null;
  onStart: (stepId: number, variant: ReplayVariant) => Promise<Replay | null>;
  onOpen: (replayId: number) => void;
}

/** "Replay from here" on a model call, and the replays made from it, each opening beside the original. */
export default function StepReplays({ stepId, canReplay, replays, error, onStart, onOpen }: StepReplaysProps) {
  const [open, setOpen] = useState(false);
  const own = replays.filter((replay) => replay.source_step_id === stepId);
  if (!canReplay && own.length === 0) return null;

  async function start(variant: ReplayVariant): Promise<boolean> {
    const started = await onStart(stepId, variant);
    if (started) onOpen(started.id);
    return started !== null;
  }

  return (
    <div className="trace-replays">
      {canReplay && !open && (
        <button type="button" className="button button-ghost" onClick={() => setOpen(true)}>
          Replay from here
        </button>
      )}
      {open && <ReplayForm onStart={start} onCancel={() => setOpen(false)} />}
      {error && <p className="form-error small">{error}</p>}
      {own.length > 0 && (
        <ul className="trace-replay-list">
          {own.map((replay) => (
            <li key={replay.id}>
              <button type="button" className="link-button" onClick={() => onOpen(replay.id)}>
                Replay #{replay.id}
              </button>{" "}
              <span className="faint small">
                {replay.variant.label} · {REPLAY_STATUS_LABELS[replay.status] ?? replay.status}
                {replay.eval_case ? ` · eval case ${replay.eval_case}` : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
