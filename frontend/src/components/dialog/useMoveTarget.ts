import { useState } from "react";

/** The choice in the move dialog: the chosen project (null is no project), and the request in flight. */
export function useMoveTarget(onMove: (projectId: number | null) => Promise<string | null>, onDone: () => void) {
  const [target, setTarget] = useState<number | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (target === undefined) return;
    setBusy(true);
    const problem = await onMove(target);
    setBusy(false);
    if (problem) setError(problem);
    else onDone();
  }

  return { target, choose: setTarget, busy, error, submit };
}
