import { useCallback, useEffect, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";
import { Segmented } from "../../components/workbench/Workbench";

type VendorState = components["schemas"]["PresetVendorOut"];

/** Which vendor the environment's presets use, and the switch that moves them all to another one. */
function usePresetVendor(environmentId: number, onSwitched: () => Promise<void>) {
  const [state, setState] = useState<VendorState | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  const reload = useCallback(async () => {
    const { data } = await api.GET("/api/v1/environments/{environment_id}/presets/vendor", {
      params: { path: { environment_id: environmentId } },
    });
    setState(data ?? null);
  }, [environmentId]);

  useEffect(() => {
    setMessage(null);
    void reload();
  }, [reload]);

  const choose = useCallback(
    async (vendor: string) => {
      setBusy(true);
      const { data, error } = await api.PUT("/api/v1/environments/{environment_id}/presets/vendor", {
        params: { path: { environment_id: environmentId } },
        body: { vendor },
      });
      setBusy(false);
      setFailed(Boolean(error));
      if (error || !data) {
        setMessage(errorMessage(error));
        return;
      }
      setState(data);
      const label = data.vendors.find((item) => item.name === vendor)?.label ?? vendor;
      setMessage(
        data.changed === 0
          ? `The presets already use ${label}.`
          : `${data.changed} ${data.changed === 1 ? "model" : "models"} moved to ${label}.`,
      );
      await onSwitched();
    },
    [environmentId, onSwitched],
  );

  return { state, busy, message, failed, choose };
}

export default function PresetVendorSwitch({
  environmentId,
  onSwitched,
}: {
  environmentId: number;
  onSwitched: () => Promise<void>;
}) {
  const { state, busy, message, failed, choose } = usePresetVendor(environmentId, onSwitched);
  if (!state || state.vendors.length < 2) return null;
  return (
    <div className="settings-vendor">
      <span className="settings-vendor-label" id="preset-vendor-label">
        Default vendor
      </span>
      <fieldset className="settings-vendor-choice" disabled={busy} aria-labelledby="preset-vendor-label">
        <Segmented
          label="Default vendor of the presets"
          value={state.current ?? ""}
          onChange={(vendor) => {
            if (vendor !== state.current) void choose(vendor);
          }}
          options={state.vendors.map((vendor) => ({
            value: vendor.name,
            label: <span title={vendor.models.join(", ")}>{vendor.label}</span>,
          }))}
        />
      </fieldset>
      <span className={failed ? "form-error" : "settings-vendor-hint"} role="status">
        {message ??
          (state.current
            ? "Switching moves every preset and the utility model to the other vendor's model of the same size."
            : "The presets mix vendors; choose one to move them all.")}
      </span>
    </div>
  );
}
