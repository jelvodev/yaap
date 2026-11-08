import { useEffect, useState } from "react";

import { api } from "../../api/client";
import type { components } from "../../api/schema";
import VaultList from "../../components/VaultList";
import { Badge, FieldRow, Panel } from "../../components/workbench/Workbench";
import { type SettingsLevel, SectionHeader } from "./shared";

/** Settings › Connections › Vault: keys and secrets of Global or an environment; a project's own are in its settings. */
export default function VaultSection({ level }: { level: SettingsLevel }) {
  const environmentId = level.kind === "environment" ? level.environment.id : undefined;
  return (
    <>
      <SectionHeader
        level={level}
        title="Vault"
        info={
          <p>
            {level.kind === "global"
              ? "Secrets every environment can use. YAAP's own keys (API keys, GitHub tokens) belong to an environment."
              : "This environment's keys and secrets, and the secrets it inherits from Global. A project can add its own in its settings."}{" "}
            Values are encrypted with the master key and never shown again; every use is logged, without the value.
          </p>
        }
      />
      <div className="wb-body wb-stack">
        <Panel title="Keys and secrets" flush>
          <VaultList key={environmentId ?? "global"} environmentId={environmentId} />
        </Panel>
        {level.kind === "global" && <MasterKeyPanel />}
      </div>
    </>
  );
}

type MasterKey = components["schemas"]["MasterKeyOut"];

/** Where the master key comes from and, during a rotation, when the previous key can go. */
function MasterKeyPanel() {
  const [state, setState] = useState<MasterKey | null>(null);
  useEffect(() => {
    void api.GET("/api/v1/vault/master-key").then(({ data }) => setState(data ?? null));
  }, []);
  if (!state) return null;
  const tone = state.unreadable ? "danger" : state.rotating ? "warn" : "ok";
  return (
    <Panel title="Master key" flush aside="Encrypts every key and secret; see docs/configuration.md to change it">
      <FieldRow label="Source" hint={state.source === "file" ? "YAAP_MASTER_KEY_FILE" : "YAAP_MASTER_KEY in .env"}>
        {state.source === "file" ? "A file (for example a Docker secret)" : "The environment"}
      </FieldRow>
      <FieldRow label="State">
        <Badge tone={tone}>{state.unreadable ? "values unreadable" : state.rotating ? "rotating" : "in use"}</Badge>{" "}
        <span className="small">{state.message}</span>
      </FieldRow>
    </Panel>
  );
}
