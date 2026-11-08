import { type Dispatch, type SetStateAction, useEffect, useRef, useState } from "react";

import { type ModelInfo, api } from "../../api/client";
import type { AccessLevel, NewConversation } from "./useActiveConversation";
import type { ChatCatalog } from "./useChatCatalog";
import type { AgentInfo } from "./useCommands";

export interface DraftOptions {
  environmentId: number;
  catalog: ChatCatalog;
  agents: AgentInfo[];
  scope: number | null;
  /** The open conversation, if any: its settings are its own, and the draft waits for the next new one. */
  conversationId: number | null;
  /** The address names a conversation (it may still be loading). */
  conversationSelected: boolean;
}

export type DraftConversation = ReturnType<typeof useDraftConversation>;

/** What a new conversation starts with: chosen in the composer before its first message. */
export function useDraftConversation(options: DraftOptions) {
  const { environmentId, catalog, agents, scope, conversationId, conversationSelected } = options;
  const { presets, projects } = catalog;
  const choice = useModelChoice(catalog);
  // The context limit a new conversation starts with; null follows the settings.
  const [limit, setLimit] = useState<number | null>(null);
  // What a new conversation may do without asking; an existing one keeps its own level.
  const [access, setAccess] = useState<AccessLevel>("default");
  // Outside a project: the computer a new conversation works on; "" is YAAP's container.
  const [target, setTarget] = useState("");
  const [pinned, setPinned] = useDefaultSkills(environmentId, scope, conversationSelected);
  // The agent a new conversation works as; "" is plain YAAP.
  const [agent, setAgent] = useState("");
  // An agent a link asks a new conversation to work as (from the Blueprint's "Start conversation").
  const requestedAgent = useRef<string | null>(null);

  function chooseAgent(name: string) {
    setAgent(name);
    const chosen = agents.find((item) => item.name === name);
    const agentPreset = chosen?.preset
      ? presets.find((item) => item.name.toLowerCase() === chosen.preset?.toLowerCase())
      : undefined;
    if (agentPreset) choice.choosePreset(String(agentPreset.id));
  }

  // A new conversation starts with the project's default agent and preset, outside a project with the preset for
  // plain chats, else with the environment's default preset.
  useEffect(() => {
    if (conversationId !== null) return;
    const project = projects.find((item) => item.id === scope);
    const start =
      (project?.default_preset && presets.find((item) => item.name === project.default_preset)) ||
      (scope === null && presets.find((item) => item.plain_chats)) ||
      presets.find((item) => item.is_default);
    if (start) choice.choosePreset(String(start.id));
    setAgent(project?.default_agent ?? "");
  }, [scope, projects, presets, conversationId]);

  useEffect(() => {
    const requested = requestedAgent.current;
    if (conversationId !== null || !requested || !agents.some((item) => item.name === requested)) return;
    requestedAgent.current = null;
    chooseAgent(requested);
  }, [agents, conversationId]);

  function newConversation(): NewConversation {
    return {
      body: {
        model: choice.model,
        effort: choice.effort,
        project_id: scope,
        agent: agent || null,
        pinned_skills: pinned,
        preset: choice.presetName,
        access,
        target_id: scope === null && target ? Number(target) : null,
        context_limit: limit,
      },
      contextWindow: choice.modelInfo?.context_window ?? null,
    };
  }

  return {
    ...choice,
    limit,
    access,
    target,
    pinned,
    agent,
    chooseAgent,
    setLimit,
    setAccess,
    setTarget,
    setPinned,
    requestAgent: (name: string) => {
      requestedAgent.current = name;
    },
    newConversation,
  };
}

/** The preset, model and effort of a new conversation; a preset sets model and effort at once. */
function useModelChoice({ loaded, models, presets }: ChatCatalog) {
  // A preset's id, or "custom" for a model and effort of the user's choice.
  const [preset, setPreset] = useState("custom");
  const [model, setModel] = useState("");
  const [effort, setEffort] = useState("");

  function choosePreset(value: string) {
    setPreset(value);
    const chosen = presets.find((item) => String(item.id) === value);
    if (chosen) {
      setModel(chosen.model);
      setEffort(chosen.effort);
    }
  }

  function chooseModel(id: string) {
    setModel(id);
    const chosen = models.find((item) => item.id === id);
    if (chosen) setEffort(chosen.default_effort);
  }

  // Before any project default applies: the environment's default preset, its first one, or its first model.
  useEffect(() => {
    if (!loaded) return;
    const start = presets.find((item) => item.is_default) ?? presets[0];
    if (start) {
      choosePreset(String(start.id));
    } else if (models[0]) {
      setPreset("custom");
      setModel(models[0].id);
      setEffort(models[0].default_effort);
    }
  }, [loaded]);

  const modelInfo: ModelInfo | undefined = models.find((item) => item.id === model);
  const presetName = presets.find((item) => String(item.id) === preset)?.name ?? null;
  return { preset, model, effort, modelInfo, presetName, choosePreset, chooseModel, setEffort };
}

/** A new conversation starts with the default skills of its environment or project pinned. */
function useDefaultSkills(
  environmentId: number,
  scope: number | null,
  conversationSelected: boolean,
): [string[], Dispatch<SetStateAction<string[]>>] {
  const [pinned, setPinned] = useState<string[]>([]);
  useEffect(() => {
    if (conversationSelected) return;
    let current = true;
    void api
      .GET("/api/v1/environments/{environment_id}/default-skills", {
        params: { path: { environment_id: environmentId }, query: { project_id: scope ?? undefined } },
      })
      .then(({ data }) => {
        if (current && data) setPinned(data.skills);
      });
    return () => {
      current = false;
    };
  }, [environmentId, scope, conversationSelected]);
  return [pinned, setPinned];
}
