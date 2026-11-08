import { useCallback, useEffect, useState } from "react";

import { type Setting, errorMessage } from "../../api/client";

const KEYS = ["reply_language", "work_language"];

function sourceLabel(source: string): string {
  return source === "default" ? "YAAP's default" : source === "global" ? "Global" : "the environment";
}

/** The project's own reply and work language, over what its environment sets. */
export default function ProjectLanguage({ projectId }: { projectId: number }) {
  const [settings, setSettings] = useState<Setting[]>([]);
  const url = `/api/v1/projects/${projectId}/settings`;

  const load = useCallback(async () => {
    const list = (await fetch(url).then((response) => response.json())) as Setting[];
    setSettings(list.filter((item) => KEYS.includes(item.key)));
  }, [url]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <>
      {settings.map((setting) => (
        <LanguageRow key={setting.key} url={`${url}/${setting.key}`} setting={setting} onChanged={load} />
      ))}
    </>
  );
}

function LanguageRow({ url, setting, onChanged }: { url: string; setting: Setting; onChanged: () => Promise<void> }) {
  const own = setting.level_value !== null && setting.level_value !== undefined;
  const stored = own ? String(setting.level_value) : "";
  const [value, setValue] = useState(stored);
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => setValue(stored), [stored]);

  async function send(method: "PUT" | "DELETE") {
    const response = await fetch(url, {
      method,
      headers: method === "PUT" ? { "Content-Type": "application/json" } : undefined,
      body: method === "PUT" ? JSON.stringify({ value: value.trim() }) : undefined,
    });
    const body = await response.json().catch(() => null);
    setMessage(response.ok ? null : errorMessage(body, `Saving failed (HTTP ${response.status}).`));
    if (response.ok) await onChanged();
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
          void send("PUT");
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
          <button type="button" className="button button-ghost" onClick={() => void send("DELETE")}>
            Use the environment's
          </button>
        )}
      </form>
      {message && <p className="form-error small">{message}</p>}
    </div>
  );
}
