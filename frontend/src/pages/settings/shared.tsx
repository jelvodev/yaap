import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";

import { type Environment, type ModelInfo, type Setting, api, errorMessage } from "../../api/client";
import { Badge, FieldRow, PageHeader } from "../../components/workbench/Workbench";
import { useSectionFrame } from "./sectionFrame";
import { useConfirm } from "../../components/dialog/ConfirmDialog";

/** The level the settings page shows: Global, or one environment. */
export type SettingsLevel = { kind: "global" } | { kind: "environment"; environment: Environment };

export function levelTitle(level: SettingsLevel): string {
  return level.kind === "global" ? "Global" : level.environment.name;
}

/** The query string that keeps a level in the URL. */
export function levelSearch(level: SettingsLevel): string {
  return level.kind === "global" ? "?level=global" : `?environment=${level.environment.id}`;
}

/** The settings of one level, with the Global ones alongside for values only Global can set. */
export interface LevelSettings {
  settings: Setting[];
  globalSettings: Setting[];
  loaded: boolean;
  find: (key: string) => Setting | undefined;
  findGlobal: (key: string) => Setting | undefined;
  save: (key: string, value: unknown) => Promise<string | null>;
  reset: (key: string) => Promise<string | null>;
  resetMany: (keys: string[]) => Promise<string | null>;
  reload: () => Promise<void>;
}

export function useLevelSettings(level: SettingsLevel): LevelSettings {
  const environmentId = level.kind === "environment" ? level.environment.id : null;
  const [settings, setSettings] = useState<Setting[]>([]);
  const [globalSettings, setGlobalSettings] = useState<Setting[]>([]);
  const [loaded, setLoaded] = useState(false);

  // Only the newest request may set the state, so a slow answer for another level never shows up here.
  const latest = useRef(0);

  const reload = useCallback(async () => {
    const request = ++latest.current;
    const globalRequest = api.GET("/api/v1/settings");
    if (environmentId === null) {
      const { data } = await globalRequest;
      if (request !== latest.current) return;
      setSettings(data ?? []);
      setGlobalSettings(data ?? []);
    } else {
      const [own, global] = await Promise.all([
        api.GET("/api/v1/environments/{environment_id}/settings", {
          params: { path: { environment_id: environmentId } },
        }),
        globalRequest,
      ]);
      if (request !== latest.current) return;
      setSettings(own.data ?? []);
      setGlobalSettings(global.data ?? []);
    }
    setLoaded(true);
  }, [environmentId]);

  useEffect(() => {
    setLoaded(false);
    setSettings([]);
    void reload();
  }, [reload]);

  const write = useCallback(
    async (key: string, value: unknown): Promise<string | null> => {
      const { error } =
        environmentId === null
          ? await api.PUT("/api/v1/settings/{key}", { params: { path: { key } }, body: { value } })
          : await api.PUT("/api/v1/environments/{environment_id}/settings/{key}", {
              params: { path: { environment_id: environmentId, key } },
              body: { value },
            });
      return error ? errorMessage(error) : null;
    },
    [environmentId],
  );

  const remove = useCallback(
    async (key: string): Promise<string | null> => {
      const { error } =
        environmentId === null
          ? await api.DELETE("/api/v1/settings/{key}", { params: { path: { key } } })
          : await api.DELETE("/api/v1/environments/{environment_id}/settings/{key}", {
              params: { path: { environment_id: environmentId, key } },
            });
      return error ? errorMessage(error) : null;
    },
    [environmentId],
  );

  const save = useCallback(
    async (key: string, value: unknown) => {
      const message = await write(key, value);
      await reload();
      return message;
    },
    [write, reload],
  );

  const reset = useCallback(
    async (key: string) => {
      const message = await remove(key);
      await reload();
      return message;
    },
    [remove, reload],
  );

  const resetMany = useCallback(
    async (keys: string[]) => {
      const messages = await Promise.all(keys.map((key) => remove(key)));
      await reload();
      return messages.find((message) => message !== null) ?? null;
    },
    [remove, reload],
  );

  return {
    settings,
    globalSettings,
    loaded,
    find: (key) => settings.find((setting) => setting.key === key),
    findGlobal: (key) => globalSettings.find((setting) => setting.key === key),
    save,
    reset,
    resetMany,
    reload,
  };
}

/** Whether the level itself sets a value (an override in an environment; a non-default at Global). */
export function isOwn(setting: Setting | undefined): boolean {
  return setting?.level_value !== null && setting?.level_value !== undefined;
}

/** The keys an environment overrides; at Global nothing is an override. */
export function overriddenKeys(level: SettingsLevel, store: LevelSettings, keys: readonly string[]): string[] {
  if (level.kind === "global") return [];
  return keys.filter((key) => isOwn(store.find(key)));
}

/** The settings an environment can override, per section (the union of its tabs) and per tab: see sections.ts. */
export { SECTION_OVERRIDE_KEYS, TAB_OVERRIDE_KEYS } from "./sections";

export function sourceName(source: Setting["source"], level: SettingsLevel): string {
  if (source === "global") return "Global";
  if (source === "environment") return level.kind === "environment" ? level.environment.name : "the environment";
  if (source === "project") return "the project";
  return "YAAP's default";
}

/** All models; with an environment, marked with whether that environment can use them. */
export function useModels(environmentId?: number, refresh?: unknown): ModelInfo[] {
  const [models, setModels] = useState<ModelInfo[]>([]);
  useEffect(() => {
    let current = true;
    const request =
      environmentId === undefined
        ? api.GET("/api/v1/models")
        : api.GET("/api/v1/environments/{environment_id}/models", {
            params: { path: { environment_id: environmentId } },
          });
    void request.then(({ data }) => current && setModels(data ?? []));
    return () => {
      current = false;
    };
  }, [environmentId, refresh]);
  return models;
}

export function formatDate(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString() : "";
}

/**
 * The header of a section: crumb, title, the old explanation behind the (i), and, in an environment, how many values
 * it overrides here with a button to drop them all. Inside a section with tabs, the title is the section's, the tabs
 * follow the header, and `title` names the tab the count and the reset belong to.
 */
export function SectionHeader({
  level,
  store,
  title,
  info,
  overrideKeys,
  badges,
  actions,
  onReset,
}: {
  level: SettingsLevel;
  store?: LevelSettings;
  title: string;
  info?: ReactNode;
  overrideKeys?: readonly string[];
  badges?: ReactNode;
  actions?: ReactNode;
  /** Called after the section was reset, for parts that keep their own state. */
  onReset?: () => void;
}) {
  const confirm = useConfirm();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const frame = useSectionFrame();
  const tabbed = Boolean(frame?.tabs);
  const overridden = store && overrideKeys ? overriddenKeys(level, store, overrideKeys) : [];

  async function resetSection() {
    if (!store || overridden.length === 0) return;
    const question = `Drop the ${overridden.length} value(s) ${levelTitle(level)} overrides in ${title}?`;
    if (!(await confirm({ message: question, confirmLabel: "Reset", tone: "danger" }))) return;
    setBusy(true);
    setMessage(await store.resetMany(overridden));
    setBusy(false);
    onReset?.();
  }

  return (
    <>
      <PageHeader
        crumb={<>Settings › {levelTitle(level)}</>}
        title={frame?.title ?? title}
        info={info}
        badges={
          <>
            {overridden.length > 0 && (
              <Badge tone="over">
                {overridden.length} overridden {tabbed ? `in ${title}` : "here"}
              </Badge>
            )}
            {badges}
          </>
        }
        actions={
          overridden.length > 0 || actions ? (
            <>
              {actions}
              {overridden.length > 0 && (
                <button type="button" className="button button-ghost" disabled={busy} onClick={() => void resetSection()}>
                  {tabbed ? `Reset ${title} to inherited` : "Reset section to inherited"}
                </button>
              )}
            </>
          ) : undefined
        }
      />
      {frame?.tabs}
      {message && <p className="form-error settings-header-message">{message}</p>}
    </>
  );
}

/**
 * One setting as a field row: the control, where the value comes from, and a reset when this level sets it.
 * `display` turns a value into readable text for the inherited value.
 */
export function SettingField({
  level,
  setting,
  control,
  display,
  onReset,
  message,
  extra,
  label,
  hint,
  ownOnly,
}: {
  level: SettingsLevel;
  setting: Setting;
  control: ReactNode;
  display: (value: unknown) => string;
  onReset: () => void;
  message?: string | null;
  extra?: ReactNode;
  label?: ReactNode;
  hint?: ReactNode;
  /** The setting exists at this level only, so nothing is inherited or overridden; reset clears it. */
  ownOnly?: boolean;
}) {
  const own = isOwn(setting);
  const inEnvironment = level.kind === "environment";
  if (ownOnly) {
    return (
      <FieldRow label={label ?? setting.label} hint={hint}>
        {control}
        {own ? (
          <button type="button" className="button button-ghost" onClick={onReset}>
            Clear
          </button>
        ) : (
          <Badge>not set</Badge>
        )}
        {setting.description && <p className="settings-help">{setting.description}</p>}
        {extra}
        {message && <p className="form-error settings-help">{message}</p>}
      </FieldRow>
    );
  }
  return (
    <FieldRow label={label ?? setting.label} hint={hint}>
      {control}
      {own ? (
        <>
          <Badge tone={inEnvironment ? "over" : "accent"}>
            {inEnvironment ? `overridden in ${levelTitle(level)}` : "set at Global"}
          </Badge>
          <button type="button" className="button button-ghost" onClick={onReset}>
            {inEnvironment ? "Reset" : "Reset to default"}
          </button>
        </>
      ) : (
        <Badge tone={inEnvironment && setting.source === "global" ? "level" : "neutral"}>
          {inEnvironment ? `inherited from ${sourceName(setting.source, level)}` : "default"}
        </Badge>
      )}
      {own && (
        <span className="settings-inherited">
          {inEnvironment ? "Inherited value" : "Default"}: {display(setting.inherited_value) || "empty"}
          {inEnvironment && ` · from ${sourceName(setting.inherited_source, level)}`}
        </span>
      )}
      {setting.description && <p className="settings-help">{setting.description}</p>}
      {extra}
      {message && <p className="form-error settings-help">{message}</p>}
    </FieldRow>
  );
}

interface Choice {
  value: string;
  label: string;
}

/** A setting with fixed choices. */
export function SelectSetting({
  level,
  setting,
  choices,
  options,
  onSave,
  onReset,
  extra,
}: {
  level: SettingsLevel;
  setting: Setting;
  /** The choices, also used to name the inherited value. */
  choices: Choice[];
  /** Custom option elements, such as grouped models; defaults to the choices. */
  options?: ReactNode;
  onSave: (value: string) => Promise<string | null>;
  onReset: () => Promise<string | null>;
  extra?: ReactNode;
}) {
  const [message, setMessage] = useState<string | null>(null);
  const display = (value: unknown) => choices.find((choice) => choice.value === value)?.label ?? String(value ?? "");
  return (
    <SettingField
      level={level}
      setting={setting}
      display={display}
      message={message}
      extra={extra}
      onReset={() => void onReset().then(setMessage)}
      control={
        <>
          <label className="visually-hidden" htmlFor={`setting-${setting.key}`}>
            {setting.label}
          </label>
          <select
            id={`setting-${setting.key}`}
            value={String(setting.value ?? "")}
            onChange={(event) => void onSave(event.target.value).then(setMessage)}
          >
            {options ??
              choices.map((choice) => (
                <option key={choice.value} value={choice.value}>
                  {choice.label}
                </option>
              ))}
          </select>
        </>
      }
    />
  );
}

/** A free-text setting with its own Save. */
export function TextSetting({
  level,
  setting,
  onSave,
  onReset,
  placeholder,
  label,
  maxLength = 100,
  inputMode,
  suffix,
  ownOnly,
}: {
  level: SettingsLevel;
  setting: Setting;
  onSave: (value: string) => Promise<string | null>;
  onReset: () => Promise<string | null>;
  placeholder?: string;
  label?: ReactNode;
  maxLength?: number;
  inputMode?: "decimal" | "text" | "email";
  suffix?: ReactNode;
  ownOnly?: boolean;
}) {
  const stored = String(setting.value ?? "");
  const [value, setValue] = useState(stored);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => setValue(stored), [stored]);

  return (
    <SettingField
      level={level}
      setting={setting}
      label={label}
      ownOnly={ownOnly}
      display={(item) => String(item ?? "")}
      message={message}
      onReset={() => void onReset().then(setMessage)}
      control={
        <form
          className="settings-inline-form"
          onSubmit={(event) => {
            event.preventDefault();
            void onSave(value).then(setMessage);
          }}
        >
          <label className="visually-hidden" htmlFor={`setting-${setting.key}`}>
            {setting.label}
          </label>
          <input
            id={`setting-${setting.key}`}
            value={value}
            maxLength={maxLength}
            placeholder={placeholder}
            inputMode={inputMode}
            onChange={(event) => setValue(event.target.value)}
          />
          {suffix}
          <button type="submit" className="button" disabled={value === stored}>
            Save
          </button>
        </form>
      }
    />
  );
}
