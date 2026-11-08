import type { ModelInfo, Preset } from "../../api/client";
import type { components } from "../../api/schema";

type RouteRole = components["schemas"]["PresetRouteOut"]["role"];

const ROUTING_HINT =
  "Per reply the utility model names the kind of message, and the role chosen for that kind answers the whole reply. " +
  "A model the model policy forbids, or of another provider than the conversation, is never picked; the main model " +
  "answers instead.";

/** The preset's model per role, as the routing selects name them; null for a role the preset leaves empty. */
function roleModels(preset: Preset): Record<RouteRole, string | null> {
  return { main: preset.model, worker: preset.worker_model ?? null, utility: preset.utility_model ?? null };
}

const ROLE_LABELS: Record<RouteRole, string> = { main: "Main", worker: "Worker", utility: "Utility" };
const ROLES: RouteRole[] = ["main", "worker", "utility"];

/** Routing of a preset: on or off, and which of its roles answers which kind of message. */
export default function PresetRouting({
  preset,
  models,
  onSave,
}: {
  preset: Preset;
  models: ModelInfo[];
  onSave: (body: components["schemas"]["PresetUpdate"]) => void;
}) {
  const byRole = roleModels(preset);
  const modelName = (id: string) => models.find((model) => model.id === id)?.display_name ?? id;
  const roleLabel = (role: RouteRole) => {
    const model = byRole[role];
    return model ? `${ROLE_LABELS[role]} · ${modelName(model)}` : `${ROLE_LABELS[role]} · none`;
  };
  return (
    <tr className="settings-preset-routing">
      <td />
      <td colSpan={6}>
        <div className="settings-routing" title={ROUTING_HINT}>
          <label className="settings-routing-toggle">
            <input
              type="checkbox"
              checked={preset.routing}
              onChange={(event) => onSave({ routing: event.target.checked })}
            />
            <span>Route each reply by the kind of message</span>
          </label>
          {preset.routing &&
            (preset.routes ?? []).map((route) => (
              <label key={route.kind} className="settings-routing-kind">
                <span>{route.label}</span>
                <select
                  value={route.role}
                  onChange={(event) => {
                    const role = ROLES.find((item) => item === event.target.value);
                    if (role) onSave({ routes: { [route.kind]: role } });
                  }}
                >
                  {ROLES.map((role) => (
                    <option key={role} value={role} disabled={!byRole[role] && role !== route.role}>
                      {roleLabel(role)}
                    </option>
                  ))}
                </select>
              </label>
            ))}
        </div>
      </td>
    </tr>
  );
}
