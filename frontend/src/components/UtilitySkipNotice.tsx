import { useEffect, useState } from "react";

import { type UtilitySkip, projectSkips } from "../api/utility-skips";

/** Which utility tasks were skipped, each with the policy's reason; nothing when none was. */
export function UtilitySkipNotice({ skips, onDismiss }: { skips: UtilitySkip[]; onDismiss?: () => void }) {
  if (skips.length === 0) return null;
  return (
    <div className="notice small" role="status">
      {skips.map((skip) => (
        <p key={skip.id}>
          <strong>{skip.label} skipped.</strong> {skip.reason}
        </p>
      ))}
      {onDismiss && (
        <button type="button" className="link-button" onClick={onDismiss}>
          Dismiss
        </button>
      )}
    </div>
  );
}

/** The project's background tasks (memory tidying, learning) whose latest attempt was skipped. */
export function ProjectSkipNotice({ projectId, tasks }: { projectId: number; tasks: string[] }) {
  const [skips, setSkips] = useState<UtilitySkip[]>([]);
  const wanted = tasks.join(",");
  useEffect(() => {
    let current = true;
    void projectSkips(projectId).then((found) => {
      if (current) setSkips(found.filter((skip) => skip.task !== null && tasks.includes(skip.task)));
    });
    return () => {
      current = false;
    };
  }, [projectId, wanted]);
  return <UtilitySkipNotice skips={skips} />;
}
