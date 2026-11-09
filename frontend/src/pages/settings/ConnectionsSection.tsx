import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { type CredentialStatus, api } from "../../api/client";
import { EmptyState, FieldRow, Panel } from "../../components/workbench/Workbench";
import ProviderPanels from "./ProviderPanels";
import { SearchIndexStatus } from "./StatusFields";
import { type LevelSettings, type SettingsLevel, SectionHeader, TAB_OVERRIDE_KEYS, levelSearch } from "./shared";

/** The credentials of an environment, reloaded on demand. */
export function useCredentials(environmentId: number | null): [CredentialStatus[], () => Promise<void>] {
  const [credentials, setCredentials] = useState<CredentialStatus[]>([]);
  const reload = useCallback(async () => {
    if (environmentId === null) {
      setCredentials([]);
      return;
    }
    const { data } = await api.GET("/api/v1/environments/{environment_id}/credentials", {
      params: { path: { environment_id: environmentId } },
    });
    setCredentials(data ?? []);
  }, [environmentId]);
  useEffect(() => {
    void reload();
  }, [reload]);
  return [credentials, reload];
}

/**
 * Connections › Providers: how YAAP reaches Claude and GPT, the keys of an environment, the CLI sign-ins and the
 * search index. MCP servers and A2A agents are tabs of their own (OutsideAgentSections).
 */
export default function ConnectionsSection({ level, store }: { level: SettingsLevel; store: LevelSettings }) {
  const environmentId = level.kind === "environment" ? level.environment.id : null;
  const [credentials, reloadCredentials] = useCredentials(environmentId);

  const embedding = store.findGlobal("embedding_model");

  return (
    <>
      <SectionHeader
        level={level}
        store={store}
        title="Providers"
        overrideKeys={TAB_OVERRIDE_KEYS.providers}
        info={
          level.kind === "global" ? (
            <p>
              How YAAP reaches each provider. What you set here applies to every environment, unless an environment
              overrides it. API keys exist only per environment, so usage is billed to the right account.
            </p>
          ) : (
            <p>
              How {level.environment.name} reaches each provider. It inherits the connection from Global; override it
              to make it different for {level.environment.name} only. Keys exist only per environment, so usage is
              billed to the right account.
            </p>
          )
        }
      />
      <div className="wb-body wb-body-narrow wb-stack">
        <ProviderPanels
          level={level}
          store={store}
          keys={environmentId === null ? null : { environmentId, credentials, reload: reloadCredentials }}
        />

        {level.kind === "global" && (
          <Panel title="API keys" flush>
            <EmptyState>
              API keys and the GitHub token are stored per environment. Switch the level at the top of the rail to an
              environment to add or replace them.
            </EmptyState>
          </Panel>
        )}

        <Panel title="Vault" flush>
          <EmptyState>
            The keys above are the default of each kind. Other keys, the agent's secrets, expiry dates and the use log
            are in the <Link to={`/settings/connections/vault${levelSearch(level)}`}>Vault</Link>.
          </EmptyState>
        </Panel>

        <Panel
          title="Search index"
          flush
          aside="Runs inside YAAP; set the model under Models › Utility and workers at Global"
        >
          <FieldRow label="Semantic search" hint={embedding ? `Model: ${String(embedding.value)}` : undefined}>
            <SearchIndexStatus setting={String(embedding?.value ?? "")} />
          </FieldRow>
        </Panel>
      </div>
    </>
  );
}
