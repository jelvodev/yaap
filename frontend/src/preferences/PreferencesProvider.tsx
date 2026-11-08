import { type ReactNode, createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import { api } from "../api/client";

/** The interface preferences: stored on the server, the theme also in the browser to avoid a flash. */
export interface Preferences {
  theme: "dark" | "light";
  utility_messages: boolean;
  verbose_tools: boolean;
}

export const DEFAULT_PREFERENCES: Preferences = { theme: "dark", utility_messages: false, verbose_tools: false };
// Read by the inline script in index.html before the page is drawn.
export const THEME_STORAGE_KEY = "yaap-theme";

interface PreferencesValue {
  preferences: Preferences;
  update: (change: Partial<Preferences>) => Promise<void>;
}

const PreferencesContext = createContext<PreferencesValue | null>(null);

function storedTheme(): Preferences["theme"] {
  try {
    return window.localStorage.getItem(THEME_STORAGE_KEY) === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

function applyTheme(theme: Preferences["theme"]) {
  document.documentElement.dataset.theme = theme;
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Without storage the server value still applies once it has loaded.
  }
}

export function PreferencesProvider({ children }: { children: ReactNode }) {
  const [preferences, setPreferences] = useState<Preferences>(() => ({ ...DEFAULT_PREFERENCES, theme: storedTheme() }));

  useEffect(() => {
    void api.GET("/api/v1/settings").then(({ data }) => {
      const value = data?.find((setting) => setting.key === "interface")?.value;
      if (value && typeof value === "object") setPreferences({ ...DEFAULT_PREFERENCES, ...(value as Partial<Preferences>) });
    });
  }, []);

  useEffect(() => applyTheme(preferences.theme), [preferences.theme]);

  const current = useRef(preferences);
  current.current = preferences;

  const update = useCallback(async (change: Partial<Preferences>) => {
    // Applied at once; the server keeps the whole set for other browsers.
    const next = { ...current.current, ...change };
    current.current = next;
    setPreferences(next);
    await api.PUT("/api/v1/settings/{key}", { params: { path: { key: "interface" } }, body: { value: next } });
  }, []);

  const value = useMemo(() => ({ preferences, update }), [preferences, update]);
  return <PreferencesContext.Provider value={value}>{children}</PreferencesContext.Provider>;
}

export function usePreferences(): PreferencesValue {
  const value = useContext(PreferencesContext);
  if (!value) throw new Error("usePreferences must be used inside PreferencesProvider.");
  return value;
}
