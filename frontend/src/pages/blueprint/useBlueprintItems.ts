import { useCallback, useEffect, useRef, useState } from "react";

import { type BlueprintItem, api } from "../../api/client";
import { type Level, levelKey, levelQuery } from "../../components/workbench/LevelPicker";
import type { Agent, AgentTemplate } from "./AgentPanels";

export interface BlueprintItems {
  columnItems: Record<string, BlueprintItem[]>;
  agents: Agent[];
  templates: AgentTemplate[];
  reloadItems: () => Promise<void>;
  reloadAgents: () => Promise<void>;
}

/** The items of every level column, plus the agents and agent templates of the environment around the level. */
export function useBlueprintItems(columnLevels: Level[], level: Level, environmentId: number | null): BlueprintItems {
  const [columnItems, setColumnItems] = useState<Record<string, BlueprintItem[]>>({});
  const [agents, setAgents] = useState<Agent[]>([]);
  const [templates, setTemplates] = useState<AgentTemplate[]>([]);

  // Only the newest load may store its result: an older one can finish later with fewer columns.
  const itemsRequest = useRef(0);
  const reloadItems = useCallback(async () => {
    const request = ++itemsRequest.current;
    const entries = await Promise.all(
      columnLevels.map(async (entry) => {
        const { data } = await api.GET("/api/v1/blueprint/items", { params: { query: levelQuery(entry) } });
        return [levelKey(entry), data ?? []] as const;
      }),
    );
    // The level being viewed may sit outside the columns (a project of another environment is not possible, but be safe).
    if (!columnLevels.some((entry) => levelKey(entry) === levelKey(level))) {
      const { data } = await api.GET("/api/v1/blueprint/items", { params: { query: levelQuery(level) } });
      entries.push([levelKey(level), data ?? []] as const);
    }
    if (request === itemsRequest.current) setColumnItems(Object.fromEntries(entries));
  }, [columnLevels, level]);

  const reloadAgents = useCallback(async () => {
    if (environmentId === null) return;
    const [agentsResult, templatesResult] = await Promise.all([
      api.GET("/api/v1/environments/{environment_id}/agents", { params: { path: { environment_id: environmentId } } }),
      api.GET("/api/v1/agent-templates"),
    ]);
    setAgents(agentsResult.data ?? []);
    setTemplates(templatesResult.data ?? []);
  }, [environmentId]);

  useEffect(() => {
    void reloadItems();
  }, [reloadItems]);

  useEffect(() => {
    void reloadAgents();
  }, [reloadAgents]);

  return { columnItems, agents, templates, reloadItems, reloadAgents };
}
