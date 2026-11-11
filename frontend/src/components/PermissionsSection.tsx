import { useCallback, useEffect, useState } from "react";

import { type Setting, api, errorMessage } from "../api/client";
import type { components } from "../api/schema";

type ToolPermission = components["schemas"]["ToolPermissionOut"];

export type PermissionLevel =
  | { kind: "global" }
  | { kind: "environment"; id: number; name: string }
  | { kind: "project"; id: number; name: string };

const KEYS = ["tool_permissions", "injection_check", "command_allowlist", "web_allowlist"];

export function settingsUrl(level: PermissionLevel): string {
  if (level.kind === "global") return "/api/v1/settings";
  if (level.kind === "environment") return `/api/v1/environments/${level.id}/settings`;
  return `/api/v1/projects/${level.id}/settings`;
}

function sourceLabel(source: string): string {
  return source === "default" ? "YAAP's default" : source === "global" ? "Global" : source === "environment" ? "the environment" : "this project";
}

async function request(method: "PUT" | "DELETE", url: string, value?: unknown): Promise<Setting> {
  const response = await fetch(url, {
    method,
    headers: value === undefined ? undefined : { "Content-Type": "application/json" },
    body: value === undefined ? undefined : JSON.stringify({ value }),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(errorMessage(body, `Saving failed (HTTP ${response.status}).`));
  return body as Setting;
}

export function ListCard({
  setting,
  level,
  placeholder,
  onChange,
}: {
  setting: Setting;
  level: PermissionLevel;
  placeholder: string;
  onChange: (setting: Setting) => void;
}) {
  const own = Array.isArray(setting.level_value) ? (setting.level_value as string[]) : null;
  const inherited = Array.isArray(setting.inherited_value) ? (setting.inherited_value as string[]) : [];
  const [text, setText] = useState((own ?? inherited).join("\n"));
  const [message, setMessage] = useState<{ error: boolean; text: string } | null>(null);
  const url = `${settingsUrl(level)}/${setting.key}`;

  useEffect(() => setText((own ?? inherited).join("\n")), [setting]);

  async function save() {
    try {
      const lines = text.split("\n").map((line) => line.trim()).filter(Boolean);
      onChange(await request("PUT", url, lines));
      setMessage({ error: false, text: "Saved." });
    } catch (error) {
      setMessage({ error: true, text: errorMessage(error) });
    }
  }

  async function reset() {
    try {
      if (level.kind === "global") {
        const resetResult = await api.DELETE("/api/v1/settings/{key}", { params: { path: { key: setting.key } } });
        if (!resetResult.response.ok) throw new Error(errorMessage(resetResult.error, "Reset failed."));
        const listResult = await api.GET("/api/v1/settings");
        if (!listResult.response.ok) throw new Error(errorMessage(listResult.error, "Loading settings failed."));
        const fresh = listResult.data?.find((item) => item.key === setting.key);
        if (!fresh) throw new Error("The reset setting could not be loaded.");
        onChange(fresh);
      } else {
        const result = level.kind === "environment"
          ? await api.DELETE("/api/v1/environments/{environment_id}/settings/{key}", {
              params: { path: { environment_id: level.id, key: setting.key } },
            })
          : await api.DELETE("/api/v1/projects/{project_id}/settings/{key}", {
              params: { path: { project_id: level.id, key: setting.key } },
            });
        if (!result.response.ok || !result.data) throw new Error(errorMessage(result.error, "Reset failed."));
        onChange(result.data);
      }
      setMessage({ error: false, text: "Back to inherited." });
    } catch (error) {
      setMessage({ error: true, text: errorMessage(error) });
    }
  }

  return (
    <div className="card">
      <div className="card-header">
        <h3>{setting.label}</h3>
        <span className="source-label">{own ? `Set here` : `Inherited from ${sourceLabel(setting.inherited_source)}`}</span>
      </div>
      <p className="muted small">{setting.description}</p>
      <label className="visually-hidden" htmlFor={`list-${setting.key}`}>
        {setting.label}
      </label>
      <textarea
        id={`list-${setting.key}`}
        className="mono list-editor"
        rows={Math.min(10, Math.max(3, text.split("\n").length + 1))}
        value={text}
        placeholder={placeholder}
        spellCheck={false}
        onChange={(event) => setText(event.target.value)}
      />
      <div className="form-row">
        <button type="button" className="button button-primary" onClick={() => void save()}>
          {own || level.kind === "global" ? "Save" : `Override for ${level.name}`}
        </button>
        {own && level.kind !== "global" && (
          <button type="button" className="button button-ghost" onClick={() => void reset()}>
            Reset to inherited
          </button>
        )}
      </div>
      {message && <p className={message.error ? "form-error" : "muted small"}>{message.text}</p>}
    </div>
  );
}

/** Which tools may run without asking, and the allowlists, at one level. */
/** A setting with fixed choices, such as the optional injection check; below global it can inherit. */
function ChoiceCard({
  setting,
  level,
  onChange,
}: {
  setting: Setting;
  level: PermissionLevel;
  onChange: (setting: Setting) => void;
}) {
  const [message, setMessage] = useState<string | null>(null);
  const url = `${settingsUrl(level)}/${setting.key}`;
  const own = setting.level_value as string | null | undefined;
  const inheritedLabel =
    setting.options.find((option) => option.value === setting.inherited_value)?.label ?? String(setting.inherited_value);

  async function choose(choice: string) {
    try {
      onChange(choice === "inherit" ? await request("DELETE", url) : await request("PUT", url, choice));
      setMessage(null);
    } catch (error) {
      setMessage(errorMessage(error));
    }
  }

  return (
    <div className="card">
      <div className="card-header">
        <h3>{setting.label}</h3>
      </div>
      <p className="muted small">{setting.description}</p>
      <label className="visually-hidden" htmlFor={`setting-${setting.key}`}>
        {setting.label}
      </label>
      <select
        id={`setting-${setting.key}`}
        value={own ?? (level.kind === "global" ? String(setting.value) : "inherit")}
        onChange={(event) => void choose(event.target.value)}
      >
        {level.kind !== "global" && (
          <option value="inherit">
            Inherited from {sourceLabel(setting.inherited_source)}: {inheritedLabel}
          </option>
        )}
        {setting.options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {message && <p className="form-error">{message}</p>}
    </div>
  );
}

export default function PermissionsSection({ level }: { level: PermissionLevel }) {
  const [settings, setSettings] = useState<Setting[]>([]);
  const [tools, setTools] = useState<ToolPermission[]>([]);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [list, catalog] = await Promise.all([
      fetch(settingsUrl(level)).then((response) => response.json() as Promise<Setting[]>),
      api.GET("/api/v1/tool-permissions").then(({ data }) => data ?? []),
    ]);
    setSettings(list.filter((setting) => KEYS.includes(setting.key)));
    setTools(catalog);
  }, [level.kind, level.kind === "global" ? 0 : level.id]);

  useEffect(() => {
    void load();
  }, [load]);

  const permissions = settings.find((setting) => setting.key === "tool_permissions");
  const own = (permissions?.level_value as Record<string, string> | null) ?? {};
  const inherited = (permissions?.inherited_value as Record<string, string> | null) ?? {};

  async function choose(tool: string, choice: string) {
    if (!permissions) return;
    const next = { ...own };
    if (choice === "inherit") delete next[tool];
    else next[tool] = choice;
    const url = `${settingsUrl(level)}/tool_permissions`;
    try {
      if (Object.keys(next).length === 0 && level.kind !== "global") await fetch(url, { method: "DELETE" });
      else await request("PUT", url, next);
      setMessage(null);
      await load();
    } catch (error) {
      setMessage(errorMessage(error));
    }
  }

  const replace = (updated: Setting) => setSettings((list) => list.map((item) => (item.key === updated.key ? updated : item)));
  const describe = (permission: string) => (permission === "auto" ? "runs automatically" : "asks first");

  return (
    <>
      {permissions && (
        <div className="card">
          <div className="card-header">
            <h3>{permissions.label}</h3>
          </div>
          <p className="muted small">{permissions.description}</p>
          <table className="runs-table permissions-table">
            <tbody>
              {tools.map((tool) => {
                const above = inherited[tool.name] ?? tool.default;
                return (
                  <tr key={tool.name}>
                    <td>
                      {tool.label}
                      <span className="faint small mono"> {tool.name}</span>
                    </td>
                    <td className="nowrap">
                      <label className="visually-hidden" htmlFor={`permission-${tool.name}`}>
                        {tool.label}
                      </label>
                      <select
                        id={`permission-${tool.name}`}
                        value={own[tool.name] ?? "inherit"}
                        onChange={(event) => void choose(tool.name, event.target.value)}
                      >
                        <option value="inherit">
                          {level.kind === "global" ? "Default" : "Inherited"}: {describe(above)}
                        </option>
                        <option value="auto">Run automatically</option>
                        <option value="ask">Ask first</option>
                      </select>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {message && <p className="form-error">{message}</p>}
        </div>
      )}
      {settings
        .filter((setting) => setting.key === "injection_check")
        .map((setting) => (
          <ChoiceCard key={setting.key} setting={setting} level={level} onChange={replace} />
        ))}
      {settings
        .filter((setting) => setting.key !== "tool_permissions" && setting.key !== "injection_check")
        .map((setting) => (
          <ListCard
            key={setting.key}
            setting={setting}
            level={level}
            placeholder={setting.key === "command_allowlist" ? "npm test\npytest" : "docs.python.org\ndeveloper.mozilla.org"}
            onChange={replace}
          />
        ))}
    </>
  );
}
