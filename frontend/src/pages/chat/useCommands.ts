import { useEffect, useMemo, useState } from "react";

import { type BlueprintItem, api } from "../../api/client";
import type { components } from "../../api/schema";

export type AgentInfo = components["schemas"]["AgentOut"];

/** A command or skill the composer offers after "/". */
export interface ComposerEntry {
  name: string;
  summary: string;
}

/**
 * The agents and Blueprint items on offer. They depend on the project: a project's own Blueprint layer can add or
 * override agents, commands and skills.
 */
export function useBlueprintOffer(
  environmentId: number,
  projectId: number | null,
): { agents: AgentInfo[]; items: BlueprintItem[] } {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [items, setItems] = useState<BlueprintItem[]>([]);

  useEffect(() => {
    void api
      .GET("/api/v1/environments/{environment_id}/agents", {
        params: { path: { environment_id: environmentId }, query: { project_id: projectId ?? undefined } },
      })
      .then(({ data }) => setAgents(data ?? []));
    void api
      .GET("/api/v1/blueprint/items", {
        params: { query: { environment_id: environmentId, project_id: projectId ?? undefined } },
      })
      .then(({ data }) => setItems(data ?? []));
  }, [environmentId, projectId]);

  return { agents, items };
}

/**
 * The commands and skills the composer offers, limited to the commands of the agent in use. goalEstimate is what a
 * goal is likely to cost, shown with /goal.
 */
export function useCommands(
  items: BlueprintItem[],
  agent: AgentInfo | undefined,
  inProject: boolean,
  goalEstimate: string | null = null,
): { commands: ComposerEntry[]; skills: ComposerEntry[] } {
  return useMemo(() => {
    const commands = items
      .filter((item) => item.kind === "command" && !item.disabled)
      .filter((item) => !agent?.commands || agent.commands.includes(item.name))
      .map((item) => ({ name: item.name, summary: item.summary }));
    // /check is built in: in a project, unless the Blueprint has its own command named check.
    if (inProject && !commands.some((entry) => entry.name === "check")) {
      commands.push({
        name: "check",
        summary: "Check the project against its standards, read only; /check <standard> for one",
      });
    }
    // /eval is built in: in a project, unless the Blueprint has its own command named eval.
    if (inProject && !commands.some((entry) => entry.name === "eval")) {
      commands.push({ name: "eval", summary: "Run the project's eval cases; /eval <case> for one" });
    }
    // /goal is built in: in a project, unless the Blueprint has its own command named goal.
    if (inProject && !commands.some((entry) => entry.name === "goal")) {
      const summary = "Work towards an objective over several turns; /goal <objective>";
      commands.push({ name: "goal", summary: goalEstimate ? `${summary}. ${goalEstimate}` : summary });
    }
    // /compact is built in, unless the Blueprint has its own command named compact.
    if (!commands.some((entry) => entry.name === "compact")) {
      commands.push({
        name: "compact",
        summary: "Summarise the earlier messages now; /compact <focus> says what to keep",
      });
    }
    const skills = items
      .filter((item) => item.kind === "skill" && !item.disabled)
      .map((item) => ({ name: item.name, summary: item.summary }));
    return { commands, skills };
  }, [items, agent, inProject, goalEstimate]);
}
