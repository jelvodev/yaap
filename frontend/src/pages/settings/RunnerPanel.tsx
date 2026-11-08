import { useCallback, useEffect, useState } from "react";

import { api, errorMessage } from "../../api/client";
import { FieldRow, Panel, Segmented } from "../../components/workbench/Workbench";

type Network = "on" | "off";

/**
 * The environment's runner: where the commands, MCP servers and browser of its projects run. It starts on
 * first use and stops after an hour unused; the only choice here is whether it can reach the internet.
 */
export default function RunnerPanel({ environmentId }: { environmentId: number }) {
  const [network, setNetwork] = useState<Network | null>(null);
  const [message, setMessage] = useState<{ error: boolean; text: string } | null>(null);

  const load = useCallback(async () => {
    const { data } = await api.GET("/api/v1/environments/{environment_id}/settings", {
      params: { path: { environment_id: environmentId } },
    });
    const value = data?.find((item) => item.key === "runner_network")?.value;
    setNetwork(value === "off" ? "off" : "on");
  }, [environmentId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function choose(value: Network) {
    setNetwork(value);
    const { error } = await api.PUT("/api/v1/environments/{environment_id}/settings/{key}", {
      params: { path: { environment_id: environmentId, key: "runner_network" } },
      body: { value },
    });
    setMessage(
      error
        ? { error: true, text: errorMessage(error) }
        : { error: false, text: "Saved. The runner is made again with this network on its next use." },
    );
    if (error) await load();
  }

  return (
    <Panel title="Runner">
      <p className="small">
        The commands, MCP servers and browser of this environment&apos;s projects run in its runner: a container that
        holds only this environment&apos;s projects and cannot reach YAAP. Projects with their own target or sandbox
        use that instead. It starts on first use and stops after an hour without use.
      </p>
      {network !== null && (
        <FieldRow label="Network" hint="Installing packages needs it.">
          <Segmented<Network>
            label="Runner network"
            value={network}
            onChange={(value) => void choose(value)}
            options={[
              { value: "on", label: "Online" },
              { value: "off", label: "Offline" },
            ]}
          />
        </FieldRow>
      )}
      {message && <p className={`small target-message${message.error ? " form-error" : ""}`}>{message.text}</p>}
    </Panel>
  );
}
