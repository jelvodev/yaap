import { useCallback, useEffect, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";

export type Task = components["schemas"]["TaskOut"];
export type TaskIn = components["schemas"]["TaskIn"];
export type TriggerConfig = components["schemas"]["TriggerConfig"];

/** A new trigger as the scheduler takes it: a scheduled task with the board_trigger action. A trigger makes no
 * model call, so its model, effort and limits are not used. */
export function triggerTask(name: string, config: TriggerConfig, cron: string, timezone: string): TaskIn {
  return {
    name,
    action: "board_trigger",
    prompt: "",
    eval_case: null,
    config,
    model: "",
    effort: "",
    agent: null,
    preset: null,
    schedule_kind: "cron",
    cron,
    timezone,
    moments: [],
    max_turns: 1,
    max_cost: 0,
    allowed_commands: config.kind === "command_failed" && config.command ? [config.command] : [],
    allowed_domains: [],
    conversation_mode: "new",
    enabled: true,
  };
}

/** The project's board triggers (its scheduled tasks with the board_trigger action) and what can be done with them. */
export function useBoardTriggers(projectId: number) {
  const [triggers, setTriggers] = useState<Task[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const path = { params: { path: { project_id: projectId } } };

  const load = useCallback(async () => {
    const result = await api.GET("/api/v1/projects/{project_id}/tasks", {
      params: { path: { project_id: projectId } },
    });
    if (result.error) {
      setError(errorMessage(result.error));
      return;
    }
    setTriggers(result.data.filter((task) => task.action === "board_trigger"));
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(action: () => Promise<{ error?: unknown }>): Promise<boolean> {
    setBusy(true);
    setError(null);
    const result = await action();
    if (result.error !== undefined) setError(errorMessage(result.error));
    await load();
    setBusy(false);
    return result.error === undefined;
  }

  const taskPath = (task: Task) => ({ params: { path: { project_id: projectId, task_id: task.id } } });

  return {
    triggers,
    error,
    busy,
    add: (body: TaskIn) => act(() => api.POST("/api/v1/projects/{project_id}/tasks", { ...path, body })),
    toggle: (task: Task) =>
      act(() =>
        api.PUT("/api/v1/projects/{project_id}/tasks/{task_id}", {
          ...taskPath(task),
          body: { ...task, enabled: !task.enabled },
        }),
      ),
    runNow: (task: Task) => act(() => api.POST("/api/v1/projects/{project_id}/tasks/{task_id}/run", taskPath(task))),
    remove: (task: Task) => act(() => api.DELETE("/api/v1/projects/{project_id}/tasks/{task_id}", taskPath(task))),
  };
}
