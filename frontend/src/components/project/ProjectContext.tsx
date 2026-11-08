import { useCallback, useEffect, useState } from "react";

import { type Setting, errorMessage } from "../../api/client";
import { CONTEXT_KEYS, contextChoices, contextLabel } from "../contextSettings";

function sourceLabel(source: string): string {
  return source === "default" ? "YAAP's default" : source === "global" ? "Global" : "the environment";
}

/** The project's own context limit and compaction settings, over what its environment sets. */
export default function ProjectContext({ projectId }: { projectId: number }) {
  const [settings, setSettings] = useState<Setting[]>([]);
  const url = `/api/v1/projects/${projectId}/settings`;

  const load = useCallback(async () => {
    const list = (await fetch(url).then((response) => response.json())) as Setting[];
    setSettings(CONTEXT_KEYS.map((key) => list.find((item) => item.key === key)).filter((item) => item !== undefined));
  }, [url]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <>
      {settings.map((setting) => (
        <ContextRow key={setting.key} url={`${url}/${setting.key}`} setting={setting} onChanged={load} />
      ))}
    </>
  );
}

function ContextRow({ url, setting, onChanged }: { url: string; setting: Setting; onChanged: () => Promise<void> }) {
  const own = setting.level_value !== null && setting.level_value !== undefined;
  const [message, setMessage] = useState<string | null>(null);
  const choices = contextChoices(setting.key, setting.value);

  async function send(method: "PUT" | "DELETE", value?: unknown) {
    const response = await fetch(url, {
      method,
      headers: method === "PUT" ? { "Content-Type": "application/json" } : undefined,
      body: method === "PUT" ? JSON.stringify({ value }) : undefined,
    });
    const body = await response.json().catch(() => null);
    setMessage(response.ok ? null : errorMessage(body, `Saving failed (HTTP ${response.status}).`));
    if (response.ok) await onChanged();
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
            void (choice ? send("PUT", choice.stored) : send("DELETE"));
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
