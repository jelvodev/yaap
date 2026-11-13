import { useState } from "react";

import { checkRemote } from "./boardApi";

export interface RemoteCheckView {
  text: string;
  tone: "good" | "warning" | "error";
}

/** The access check of a board repository's URL: busy flag, last result, and a reset for when the URL changes. */
export function useRemoteCheck(projectId: number) {
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState<RemoteCheckView | null>(null);

  async function check(url: string) {
    setBusy(true);
    const answer = await checkRemote(projectId, url);
    setBusy(false);
    if (!answer.ok) {
      setView({ text: answer.problem.message, tone: "error" });
    } else if (!answer.data.ok) {
      setView({ text: answer.data.message ?? "The repository cannot be reached.", tone: "error" });
    } else if (answer.data.private === true) {
      setView({ text: "Reachable, private.", tone: "good" });
    } else if (answer.data.private === false) {
      setView({ text: "Reachable, but public: the board's cards would be public.", tone: "warning" });
    } else {
      setView({ text: "Reachable; YAAP cannot tell whether it is private.", tone: "good" });
    }
  }

  return { busy, view, check, reset: () => setView(null) };
}
