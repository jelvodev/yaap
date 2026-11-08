import { useEffect, useState } from "react";

import { api } from "../../api/client";
import { estimateText } from "../../components/costEstimate";

/**
 * What a goal in the project, worked on as the agent, is likely to cost, as one line from the project's earlier
 * goals; null outside a project or while it loads. Loaded again when a goal starts or ends (goalKey).
 */
export function useGoalEstimate(projectId: number | null, agent: string | null, goalKey: string): string | null {
  const [text, setText] = useState<string | null>(null);

  useEffect(() => {
    setText(null);
    if (projectId === null) return;
    let current = true;
    void api
      .GET("/api/v1/projects/{project_id}/goal-estimate", {
        params: { path: { project_id: projectId }, query: { agent: agent ?? undefined } },
      })
      .then(({ data }) => {
        if (current && data) setText(estimateText(data, "goals"));
      });
    return () => {
      current = false;
    };
  }, [projectId, agent, goalKey]);

  return text;
}
