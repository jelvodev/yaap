import { useCallback, useEffect, useState } from "react";

import { type ModelInfo, type Setting, api, errorMessage } from "../api/client";
import type { components } from "../api/schema";

type Provider = components["schemas"]["ProviderOut"];

/** The setting model_policy at one level; a missing key leaves that part open, or inherits it in a project. */
export interface PolicyValue {
  connections?: string[];
  models?: string[] | "local";
  business_data?: boolean;
  agents?: string[];
  patterns?: string[];
}

export type PolicyLevel =
  | { kind: "environment"; environmentId: number; name: string }
  | { kind: "project"; environmentId: number; projectId: number; name: string };

export interface PolicyConnection {
  key: string;
  provider: string;
  label: string;
  local: boolean;
}

function names(value: unknown): string[] | undefined {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : undefined;
}

/** A stored value as a policy, keeping only the fields of the right shape. */
export function toPolicy(value: unknown): PolicyValue {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const source = value as Record<string, unknown>;
  const policy: PolicyValue = {};
  const connections = names(source.connections);
  if (connections) policy.connections = connections;
  if (source.models === "local") policy.models = "local";
  else if (names(source.models)) policy.models = names(source.models);
  if (typeof source.business_data === "boolean") policy.business_data = source.business_data;
  const agents = names(source.agents);
  if (agents) policy.agents = agents;
  const patterns = names(source.patterns);
  if (patterns) policy.patterns = patterns;
  return policy;
}

export function connectionKey(provider: Provider): string {
  return `${provider.name}:${provider.connection}`;
}

async function levelSettings(level: PolicyLevel): Promise<Setting[]> {
  if (level.kind === "environment") {
    const { data } = await api.GET("/api/v1/environments/{environment_id}/settings", {
      params: { path: { environment_id: level.environmentId } },
    });
    return data ?? [];
  }
  const { data } = await api.GET("/api/v1/projects/{project_id}/settings", {
    params: { path: { project_id: level.projectId } },
  });
  return data ?? [];
}

/** The policy of one level with what it can name: connections, models and the environment's A2A agents. */
export function useModelPolicy(level: PolicyLevel) {
  const [own, setOwn] = useState<PolicyValue | null>(null);
  const [inherited, setInherited] = useState<PolicyValue>({});
  const [connections, setConnections] = useState<PolicyConnection[]>([]);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [agents, setAgents] = useState<string[]>([]);
  const [loaded, setLoaded] = useState(false);
  const projectId = level.kind === "project" ? level.projectId : null;

  const load = useCallback(async () => {
    const [settings, environmentSettings, providers, modelList] = await Promise.all([
      levelSettings(level),
      levelSettings({ kind: "environment", environmentId: level.environmentId, name: "" }),
      api.GET("/api/v1/providers").then(({ data }) => data ?? []),
      api
        .GET("/api/v1/environments/{environment_id}/models", { params: { path: { environment_id: level.environmentId } } })
        .then(({ data }) => data ?? []),
    ]);
    const setting = settings.find((item) => item.key === "model_policy");
    setOwn(setting?.level_value === null || setting?.level_value === undefined ? null : toPolicy(setting.level_value));
    setInherited(level.kind === "project" ? toPolicy(setting?.inherited_value) : {});
    setConnections(providers.map((provider) => ({
        key: connectionKey(provider),
        provider: provider.name,
        label: provider.display_name,
        local: provider.local ?? false,
      })));
    setModels(modelList);
    const agentSetting = environmentSettings.find((item) => item.key === "a2a_agents")?.value;
    setAgents(agentSetting && typeof agentSetting === "object" ? Object.keys(agentSetting).sort() : []);
    setLoaded(true);
  }, [level.kind, level.environmentId, projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function save(value: PolicyValue): Promise<string | null> {
    const { error } =
      level.kind === "environment"
        ? await api.PUT("/api/v1/environments/{environment_id}/settings/{key}", {
            params: { path: { environment_id: level.environmentId, key: "model_policy" } },
            body: { value },
          })
        : await api.PUT("/api/v1/projects/{project_id}/settings/{key}", {
            params: { path: { project_id: level.projectId, key: "model_policy" } },
            body: { value },
          });
    if (error) return errorMessage(error, "Saving the policy failed.");
    await load();
    return null;
  }

  async function reset(): Promise<string | null> {
    const { error } =
      level.kind === "environment"
        ? await api.DELETE("/api/v1/environments/{environment_id}/settings/{key}", {
            params: { path: { environment_id: level.environmentId, key: "model_policy" } },
          })
        : await api.DELETE("/api/v1/projects/{project_id}/settings/{key}", {
            params: { path: { project_id: level.projectId, key: "model_policy" } },
          });
    if (error) return errorMessage(error, "Removing the policy failed.");
    await load();
    return null;
  }

  return { own, inherited, connections, models, agents, loaded, save, reset };
}
