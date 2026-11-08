import { useEffect, useState } from "react";

import { api } from "../../api/client";
import { estimateText } from "../costEstimate";

/** What a run of the scheduled task is likely to cost, from its earlier runs; loaded again after each run. */
export default function TaskEstimate({
  projectId,
  taskId,
  lastRunAt,
}: {
  projectId: number;
  taskId: number;
  lastRunAt: string | null | undefined;
}) {
  const [text, setText] = useState<string | null>(null);
  useEffect(() => {
    let current = true;
    void api
      .GET("/api/v1/projects/{project_id}/tasks/{task_id}/estimate", {
        params: { path: { project_id: projectId, task_id: taskId } },
      })
      .then(({ data }) => {
        if (current) setText(data ? estimateText(data, "runs") : null);
      });
    return () => {
      current = false;
    };
  }, [projectId, taskId, lastRunAt]);
  return text ? <p className="faint small">{text}</p> : null;
}
