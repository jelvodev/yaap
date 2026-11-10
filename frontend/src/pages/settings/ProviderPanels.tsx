import type { CredentialStatus } from "../../api/client";
import { Panel } from "../../components/workbench/Workbench";
import CredentialField from "./CredentialField";
import EndpointsPanel from "./EndpointsPanel";
import { ClaudeCodeStatusField, CodexStatusField } from "./StatusFields";
import { type LevelSettings, type SettingsLevel, SelectSetting, TextSetting } from "./shared";

const CONNECTION_KEY = "anthropic_connection";
const OPENAI_CONNECTION_KEY = "openai_connection";

/** The keys of one environment, shown under the provider they belong to. */
export interface ProviderKeys {
  environmentId: number;
  credentials: CredentialStatus[];
  reload: () => Promise<void>;
}

/**
 * How YAAP reaches each provider at a level: the connection per vendor, the CLI sign-ins, the keys of an environment
 * and, at Global, Ollama. Used by Settings › Connections and by the first start.
 */
export default function ProviderPanels({
  level,
  store,
  keys,
}: {
  level: SettingsLevel;
  store: LevelSettings;
  /** The environment whose keys to show; none at Global, where keys do not exist. */
  keys: ProviderKeys | null;
}) {
  const connection = store.find(CONNECTION_KEY);
  const openaiConnection = store.find(OPENAI_CONNECTION_KEY);
  const ollamaUrl = store.find("ollama_url");
  const usesClaudeCode = connection?.value === "claude_code";
  const usesCodex = openaiConnection?.value === "codex";

  const credentials = keys?.credentials ?? [];
  const anthropicKeys = credentials.filter((credential) => credential.kind === "anthropic_api_key");
  // With Claude Code or Codex, a stored key is still shown so it can be removed.
  const visibleAnthropic = usesClaudeCode ? anthropicKeys.filter((credential) => credential.configured) : anthropicKeys;
  const visibleOpenai = credentials.filter(
    (credential) => credential.kind === "openai_api_key" && (!usesCodex || credential.configured),
  );

  return (
    <>
      <Panel title="Anthropic" flush>
        {connection && (
          <SelectSetting
            level={level}
            setting={connection}
            choices={connection.options}
            onSave={(value) => store.save(CONNECTION_KEY, value)}
            onReset={() => store.reset(CONNECTION_KEY)}
          />
        )}
        {(level.kind === "global" || usesClaudeCode) && <ClaudeCodeStatusField />}
        {keys &&
          visibleAnthropic.map((credential) => (
            <CredentialField
              key={`${keys.environmentId}-${credential.kind}`}
              environmentId={keys.environmentId}
              credential={credential}
              onChanged={keys.reload}
              note={usesClaudeCode ? "Not used while this environment connects through Claude Code." : undefined}
            />
          ))}
      </Panel>

      <Panel title="OpenAI" flush aside="GPT models run as agents with YAAP's tools, like Claude">
        {openaiConnection && (
          <SelectSetting
            level={level}
            setting={openaiConnection}
            choices={openaiConnection.options}
            onSave={(value) => store.save(OPENAI_CONNECTION_KEY, value)}
            onReset={() => store.reset(OPENAI_CONNECTION_KEY)}
          />
        )}
        {usesCodex && <CodexStatusField />}
        {keys &&
          visibleOpenai.map((credential) => (
            <CredentialField
              key={`${keys.environmentId}-${credential.kind}`}
              environmentId={keys.environmentId}
              credential={credential}
              onChanged={keys.reload}
              note={usesCodex ? "Not used while this environment connects through Codex." : undefined}
            />
          ))}
      </Panel>

      {level.kind === "global" && ollamaUrl && (
        <Panel title="Ollama" flush aside="Local models, without cost; they run as agents with YAAP's tools">
          <TextSetting
            level={level}
            setting={ollamaUrl}
            placeholder="http://host.docker.internal:11434"
            maxLength={200}
            ownOnly
            onSave={(value) => store.save("ollama_url", value.trim())}
            onReset={() => store.reset("ollama_url")}
          />
        </Panel>
      )}

      {level.kind === "global" && <EndpointsPanel store={store} />}
    </>
  );
}
