import { useEffect, useState } from "react";

import { type UtilitySkip, conversationSkips } from "../../api/utility-skips";

export interface UtilitySkips {
  /** The skipped tasks of the last exchange that the user has not dismissed. */
  shown: UtilitySkip[];
  /** A skip the reply stream reported. */
  add: (skip: UtilitySkip) => void;
  dismiss: () => void;
}

/**
 * The utility tasks the model policy skipped in the open conversation's last exchange: read from their audit rows
 * when the conversation opens or its messages change (work that ended after the reply), and added as the reply
 * stream reports them.
 */
export function useUtilitySkips(conversationId: number | null, refreshKey: string): UtilitySkips {
  const [skips, setSkips] = useState<UtilitySkip[]>([]);
  const [dismissed, setDismissed] = useState<Set<number>>(new Set());

  useEffect(() => {
    setDismissed(new Set());
  }, [conversationId]);

  useEffect(() => {
    if (conversationId === null) {
      setSkips([]);
      return;
    }
    let current = true;
    void conversationSkips(conversationId).then((found) => current && setSkips(found));
    return () => {
      current = false;
    };
  }, [conversationId, refreshKey]);

  return {
    shown: skips.filter((skip) => !dismissed.has(skip.id)),
    add: (skip) => setSkips((earlier) => (earlier.some((item) => item.id === skip.id) ? earlier : [...earlier, skip])),
    dismiss: () => setDismissed(new Set([...dismissed, ...skips.map((skip) => skip.id)])),
  };
}
