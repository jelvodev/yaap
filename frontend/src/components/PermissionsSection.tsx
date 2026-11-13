import { useEffect, useState } from "react";

import { type Setting, api } from "../api/client";
import type { components } from "../api/schema";
import { type SettingsScope, useScopedSettings } from "../pages/settings/useScopedSettings";

type ToolPermission = components["schemas"]["ToolPermissionOut"];

export type PermissionLevel =
  | { kind: "global" }
  | { kind: "environment"; id: number; name: string }
  | { kind: "project"; id: number; name: string };

const KEYS = ["tool_permissions", "injection_check", "command_allowlist", "web_allowlist"];

/** The settings scope a permission level edits. */
export function scopeOf(level: PermissionLevel): SettingsScope {
  return level.kind === "global" ? { kind: "global" } : { kind: level.kind, id: level.id };
}

function sourceLabel(source: string): string {
  return source === "default" ? "YAAP's default" : source === "global" ? "Global" : source === "environment" ? "the environment" : "this project";
}

export function ListCard({
  setting,
  level,
  placeholder,
  onSave,
  onReset,
}: {
  setting: Setting;
  level: PermissionLevel;
  placeholder: string;
  onSave: (lines: string[]) => Promise<string | null>;
  onReset: () => Promise<string | null>;
}) {
  const own = Array.isArray(setting.level_value) ? (setting.level_value as string[]) : null;
  const inherited = Array.isArray(setting.inherited_value) ? (setting.inherited_value as string[]) : [];
  const [text, setText] = useState((own ?? inherited).join("\n"));
  const [message, setMessage] = useState<{ error: boolean; text: string } | null>(null);

  useEffect(() => setText((own ?? inherited).join("\n")), [setting]);

  async function save() {
    const error = await onSave(text.split("\n").map((line) => line.trim()).filter(Boolean));
    setMessage(error ? { error: true, text: error } : { error: false, text: "Saved." });
  }

  async function reset() {
    const error = await onReset();
    setMessage(error ? { error: true, text: error } : { error: false, text: "Back to inherited." });
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
  onSet,
  onReset,
}: {
  setting: Setting;
  level: PermissionLevel;
  onSet: (value: string) => Promise<string | null>;
  onReset: () => Promise<string | null>;
}) {
  const [message, setMessage] = useState<string | null>(null);
  const own = setting.level_value as string | null | undefined;
  const inheritedLabel =
    setting.options.find((option) => option.value === setting.inherited_value)?.label ?? String(setting.inherited_value);

  async function choose(choice: string) {
    setMessage(await (choice === "inherit" ? onReset() : onSet(choice)));
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
  const { settings: all, set, reset } = useScopedSettings(scopeOf(level));
  const settings = all.filter((setting) => KEYS.includes(setting.key));
  const [tools, setTools] = useState<ToolPermission[]>([]);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    void api.GET("/api/v1/tool-permissions").then(({ data }) => setTools(data ?? []));
  }, []);

  const permissions = settings.find((setting) => setting.key === "tool_permissions");
  const own = (permissions?.level_value as Record<string, string> | null) ?? {};
  const inherited = (permissions?.inherited_value as Record<string, string> | null) ?? {};

  async function choose(tool: string, choice: string) {
    if (!permissions) return;
    const next = { ...own };
    if (choice === "inherit") delete next[tool];
    else next[tool] = choice;
    const error =
      Object.keys(next).length === 0 && level.kind !== "global"
        ? await reset("tool_permissions")
        : await set("tool_permissions", next);
    setMessage(error);
  }

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
          <ChoiceCard
            key={setting.key}
            setting={setting}
            level={level}
            onSet={(value) => set(setting.key, value)}
            onReset={() => reset(setting.key)}
          />
        ))}
      {settings
        .filter((setting) => setting.key !== "tool_permissions" && setting.key !== "injection_check")
        .map((setting) => (
          <ListCard
            key={setting.key}
            setting={setting}
            level={level}
            placeholder={setting.key === "command_allowlist" ? "npm test\npytest" : "docs.python.org\ndeveloper.mozilla.org"}
            onSave={(lines) => set(setting.key, lines)}
            onReset={() => reset(setting.key)}
          />
        ))}
    </>
  );
}
