import type { Environment } from "../../api/client";
import { Panel } from "../../components/workbench/Workbench";
import type { StepState } from "../../firstRun/useFirstRun";
import { useCredentials } from "../settings/ConnectionsSection";
import ProviderPanels from "../settings/ProviderPanels";
import { type SettingsLevel, useLevelSettings } from "../settings/shared";

const GLOBAL: SettingsLevel = { kind: "global" };

/**
 * A way to reach a model: the connection per provider for every environment, and a key stored in the environment
 * the user starts in.
 */
export default function ConnectionStep({
  environment,
  state,
  onChanged,
}: {
  environment: Environment;
  state: StepState;
  onChanged: () => void;
}) {
  const store = useLevelSettings(GLOBAL);
  const [credentials, reload] = useCredentials(environment.id);

  async function reloadKeys() {
    await reload();
    onChanged();
  }

  return (
    <>
      <Panel title="How YAAP reaches a model">
        <p>
          Choose a connection per provider: an API key, or the sign-in of Claude Code or Codex with your subscription.
          Ollama runs local models. One working connection is enough.
        </p>
        <p className="muted small">
          The connection applies to every environment. A key is stored in {environment.name} only; add keys for other
          environments later in Settings › Connections.
        </p>
        {state === "done" ? (
          <p className="form-ok small">A connection works.</p>
        ) : (
          <p className="form-error small">No working connection yet. Without one, YAAP cannot answer.</p>
        )}
      </Panel>
      {store.loaded && (
        <ProviderPanels
          level={GLOBAL}
          store={store}
          keys={{ environmentId: environment.id, credentials, reload: reloadKeys }}
        />
      )}
    </>
  );
}
