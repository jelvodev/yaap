import { useEffect, useState } from "react";

import type { Setting } from "../../api/client";
import { useScopedSettings } from "../../pages/settings/useScopedSettings";

const KEYS = ["reply_language", "work_language"];

function sourceLabel(source: string): string {
  return source === "default" ? "YAAP's default" : source === "global" ? "Global" : "the environment";
}

/** The project's own reply and work language, over what its environment sets. */
export default function ProjectLanguage({ projectId }: { projectId: number }) {
  const { settings, set, reset } = useScopedSettings({ kind: "project", id: projectId });

  return (
    <>
      {settings
        .filter((setting) => KEYS.includes(setting.key))
        .map((setting) => (
          <LanguageRow
            key={setting.key}
            setting={setting}
            onSave={(value) => set(setting.key, value)}
            onReset={() => reset(setting.key)}
          />
        ))}
    </>
  );
}

function LanguageRow({
  setting,
  onSave,
  onReset,
}: {
  setting: Setting;
  onSave: (value: string) => Promise<string | null>;
  onReset: () => Promise<string | null>;
}) {
  const own = setting.level_value !== null && setting.level_value !== undefined;
  const stored = own ? String(setting.level_value) : "";
  const [value, setValue] = useState(stored);
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => setValue(stored), [stored]);

  async function send(action: () => Promise<string | null>) {
    setMessage(await action());
  }

  const inherited = String(setting.inherited_value ?? "") || "the language of the message";
  return (
    <div className="pp-language">
      <label htmlFor={`project-${setting.key}`}>{setting.label}</label>
      <p className="faint small">
        {setting.description}{" "}
        {own ? "Set for this project." : `Now ${inherited}, from ${sourceLabel(setting.inherited_source)}.`}
      </p>
      <form
        className="pp-row"
        onSubmit={(event) => {
          event.preventDefault();
          void send(() => onSave(value.trim()));
        }}
      >
        <input
          id={`project-${setting.key}`}
          value={value}
          maxLength={40}
          placeholder={own ? "" : inherited}
          onChange={(event) => setValue(event.target.value)}
        />
        <button type="submit" className="button" disabled={value.trim() === stored}>
          Save
        </button>
        {own && (
          <button type="button" className="button button-ghost" onClick={() => void send(onReset)}>
            Use the environment's
          </button>
        )}
      </form>
      {message && <p className="form-error small">{message}</p>}
    </div>
  );
}
