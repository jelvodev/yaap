import { useCallback, useEffect, useRef, useState } from "react";

import { type Setting, api, errorMessage } from "../../api/client";

/** The level whose own settings are read and written: Global, one environment or one project. */
export type SettingsScope =
  | { kind: "global" }
  | { kind: "environment"; id: number }
  | { kind: "project"; id: number };

/** The outcome of one settings request: the data, or a readable error. */
export type SettingsResult<T> = { ok: true; data: T } | { ok: false; error: string };

function scopeKey(scope: SettingsScope): string {
  return scope.kind === "global" ? "global" : `${scope.kind}:${scope.id}`;
}

function failed<T>(error: unknown, fallback: string): SettingsResult<T> {
  return { ok: false, error: errorMessage(error, fallback) };
}

/** Every setting of a scope, each resolved at that scope. */
export async function listSettings(scope: SettingsScope): Promise<SettingsResult<Setting[]>> {
  const { data, error } =
    scope.kind === "global"
      ? await api.GET("/api/v1/settings")
      : scope.kind === "environment"
        ? await api.GET("/api/v1/environments/{environment_id}/settings", {
            params: { path: { environment_id: scope.id } },
          })
        : await api.GET("/api/v1/projects/{project_id}/settings", { params: { path: { project_id: scope.id } } });
  return data ? { ok: true, data } : failed(error, "Loading settings failed.");
}

/** Store a value at the scope; the answer is the setting resolved there. */
export async function putSetting(scope: SettingsScope, key: string, value: unknown): Promise<SettingsResult<Setting>> {
  const { data, error } =
    scope.kind === "global"
      ? await api.PUT("/api/v1/settings/{key}", { params: { path: { key } }, body: { value } })
      : scope.kind === "environment"
        ? await api.PUT("/api/v1/environments/{environment_id}/settings/{key}", {
            params: { path: { environment_id: scope.id, key } },
            body: { value },
          })
        : await api.PUT("/api/v1/projects/{project_id}/settings/{key}", {
            params: { path: { project_id: scope.id, key } },
            body: { value },
          });
  return data ? { ok: true, data } : failed(error, "Saving failed.");
}

/**
 * Drop the scope's own value. An environment or project answers with the setting resolved again; Global answers
 * with no content, so its resolved value (the default) is read from the list afterwards.
 */
export async function deleteSetting(scope: SettingsScope, key: string): Promise<SettingsResult<Setting>> {
  if (scope.kind === "global") {
    const { response, error } = await api.DELETE("/api/v1/settings/{key}", { params: { path: { key } } });
    if (!response.ok) return failed(error, "Reset failed.");
    const list = await listSettings(scope);
    if (!list.ok) return list;
    const fresh = list.data.find((item) => item.key === key);
    return fresh ? { ok: true, data: fresh } : { ok: false, error: "The reset setting could not be loaded." };
  }
  const { data, error } =
    scope.kind === "environment"
      ? await api.DELETE("/api/v1/environments/{environment_id}/settings/{key}", {
          params: { path: { environment_id: scope.id, key } },
        })
      : await api.DELETE("/api/v1/projects/{project_id}/settings/{key}", {
          params: { path: { project_id: scope.id, key } },
        });
  return data ? { ok: true, data } : failed(error, "Reset failed.");
}

/** The settings of one scope, kept current after each change made through it. */
export interface ScopedSettings {
  settings: Setting[];
  loaded: boolean;
  /** Why the list could not be loaded, if it could not. */
  error: string | null;
  find: (key: string) => Setting | undefined;
  /** Store a value; null on success, otherwise the error to show. */
  set: (key: string, value: unknown) => Promise<string | null>;
  /** Drop the scope's own value; null on success, otherwise the error to show. */
  reset: (key: string) => Promise<string | null>;
  reload: () => Promise<void>;
}

/**
 * The settings of a scope through the typed client. An answer that arrives after the scope changed (another project
 * selected) or after a newer load is ignored, so it never shows up under the wrong scope.
 */
export function useScopedSettings(scope: SettingsScope): ScopedSettings {
  const key = scopeKey(scope);
  const [settings, setSettings] = useState<Setting[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const current = useRef(key);
  const latest = useRef(0);
  // The scope as an object for the callbacks; the string key decides when it changed.
  const scopeRef = useRef(scope);
  scopeRef.current = scope;
  current.current = key;

  const reload = useCallback(async () => {
    const request = ++latest.current;
    const result = await listSettings(scopeRef.current);
    if (request !== latest.current || current.current !== key) return;
    if (result.ok) {
      setSettings(result.data);
      setError(null);
    } else {
      setError(result.error);
    }
    setLoaded(true);
  }, [key]);

  useEffect(() => {
    setLoaded(false);
    setSettings([]);
    setError(null);
    void reload();
  }, [reload]);

  const apply = useCallback(
    (result: SettingsResult<Setting>): string | null => {
      if (current.current !== key) return null;
      if (!result.ok) return result.error;
      const updated = result.data;
      setSettings((list) => list.map((item) => (item.key === updated.key ? updated : item)));
      return null;
    },
    [key],
  );

  const set = useCallback(
    async (settingKey: string, value: unknown) => apply(await putSetting(scopeRef.current, settingKey, value)),
    [apply],
  );

  const reset = useCallback(
    async (settingKey: string) => apply(await deleteSetting(scopeRef.current, settingKey)),
    [apply],
  );

  const find = useCallback((settingKey: string) => settings.find((item) => item.key === settingKey), [settings]);

  return { settings, loaded, error, find, set, reset, reload };
}
