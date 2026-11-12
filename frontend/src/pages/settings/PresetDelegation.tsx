import type { Preset } from "../../api/client";
import type { components } from "../../api/schema";

type DelegationMode = components["schemas"]["PresetOut"]["delegation"];

const DELEGATION_HINT =
  "Free: the lead model delegates as it sees fit. By brief: it hands over checkable work with a brief (files, " +
  "facts, done-when, a check command); YAAP runs the check after the worker, a failed check goes back to the " +
  "worker once, and a second failure hands the task to the main model. Exploring sub-runs may run up to three " +
  "at once.";

const MODES: { value: DelegationMode; label: string }[] = [
  { value: "free", label: "Free" },
  { value: "brief", label: "By brief" },
];

/** How the preset's lead model hands work to workers: free, or by brief with a check after the worker. */
export default function PresetDelegation({
  preset,
  onSave,
}: {
  preset: Preset;
  onSave: (body: components["schemas"]["PresetUpdate"]) => void;
}) {
  return (
    <tr className="settings-preset-routing">
      <td />
      <td colSpan={6}>
        <div className="settings-routing" title={DELEGATION_HINT}>
          <label className="settings-routing-kind">
            <span>Delegation</span>
            <select
              value={preset.delegation}
              onChange={(event) => {
                const mode = MODES.find((item) => item.value === event.target.value);
                if (mode) onSave({ delegation: mode.value });
              }}
            >
              {MODES.map((mode) => (
                <option key={mode.value} value={mode.value}>
                  {mode.label}
                </option>
              ))}
            </select>
          </label>
          <span className="muted">
            {preset.delegation === "brief"
              ? "Workers get a brief and YAAP checks their work; a second failure goes to the main model."
              : "The lead model delegates as it sees fit."}
          </span>
        </div>
      </td>
    </tr>
  );
}
