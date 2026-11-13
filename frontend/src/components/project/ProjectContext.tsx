import { useState } from "react";

import type { Setting } from "../../api/client";
import { useScopedSettings } from "../../pages/settings/useScopedSettings";
import { CONTEXT_KEYS, contextChoices, contextLabel } from "../contextSettings";

function sourceLabel(source: string): string {
  return source === "default" ? "YAAP's default" : source === "global" ? "Global" : "the environment";
}

/** The project's own context limit and compaction settings, over what its environment sets. */
export default function ProjectContext({ projectId }: { projectId: number }) {
  const { settings, set, reset } = useScopedSettings({ kind: "project", id: projectId });

  return (
    <>
      {CONTEXT_KEYS.map((key) => settings.find((item) => item.key === key))
        .filter((item) => item !== undefined)
        .map((setting) => (
          <ContextRow
            key={setting.key}
            setting={setting}
            onSave={(value) => set(setting.key, value)}
            onReset={() => reset(setting.key)}
          />
        ))}
    </>
  );
}

function ContextRow({
  setting,
  onSave,
  onReset,
}: {
  setting: Setting;
  onSave: (value: unknown) => Promise<string | null>;
  onReset: () => Promise<string | null>;
}) {
  const own = setting.level_value !== null && setting.level_value !== undefined;
  const [message, setMessage] = useState<string | null>(null);
  const choices = contextChoices(setting.key, setting.value);

  async function send(action: () => Promise<string | null>) {
    setMessage(await action());
  }

  return (
    <div className="pp-language">
      <label htmlFor={`project-${setting.key}`}>{setting.label}</label>
      <p className="faint small">
        {setting.description}{" "}
        {own
          ? "Set for this project."
          : `Now ${contextLabel(setting.key, setting.inherited_value)}, from ${sourceLabel(setting.inherited_source)}.`}
      </p>
      <div className="pp-row">
        <select
          id={`project-${setting.key}`}
          value={own ? String(setting.level_value) : ""}
          onChange={(event) => {
            const choice = choices.find((item) => item.value === event.target.value);
            void (choice ? send(() => onSave(choice.stored)) : send(onReset));
          }}
        >
          <option value="">Inherit: {contextLabel(setting.key, setting.inherited_value)}</option>
          {choices.map((choice) => (
            <option key={choice.value} value={choice.value}>
              {choice.label}
            </option>
          ))}
        </select>
      </div>
      {message && <p className="form-error small">{message}</p>}
    </div>
  );
}
