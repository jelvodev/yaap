import { type ReactNode, createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { type Environment, api, errorMessage } from "../api/client";

const STORAGE_KEY = "yaap.activeEnvironmentId";

interface EnvironmentContextValue {
  environments: Environment[];
  active: Environment | null;
  error: string | null;
  selectEnvironment: (id: number) => void;
  reloadEnvironments: () => Promise<void>;
}

const EnvironmentContext = createContext<EnvironmentContextValue | null>(null);

function readStoredId(): number | null {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return value ? Number(value) : null;
  } catch {
    return null;
  }
}

function storeId(id: number): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, String(id));
  } catch {
    // Storage can be unavailable; the choice then lasts for this session only.
  }
}

export function EnvironmentProvider({ children }: { children: ReactNode }) {
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [activeId, setActiveId] = useState<number | null>(readStoredId);
  const [error, setError] = useState<string | null>(null);

  const reloadEnvironments = useCallback(async () => {
    const { data, error: requestError } = await api.GET("/api/v1/environments");
    if (requestError || !data) {
      setError(errorMessage(requestError, "Environments could not be loaded."));
      return;
    }
    setError(null);
    setEnvironments(data);
  }, []);

  useEffect(() => {
    void reloadEnvironments();
  }, [reloadEnvironments]);

  const active = useMemo(
    () => environments.find((environment) => environment.id === activeId) ?? environments[0] ?? null,
    [environments, activeId],
  );

  useEffect(() => {
    // The active environment's colour is the accent of the whole interface.
    document.documentElement.style.setProperty("--accent", active?.colour ?? "#5B8CFF");
  }, [active]);

  const selectEnvironment = useCallback((id: number) => {
    setActiveId(id);
    storeId(id);
  }, []);

  const value = useMemo(
    () => ({ environments, active, error, selectEnvironment, reloadEnvironments }),
    [environments, active, error, selectEnvironment, reloadEnvironments],
  );
  return <EnvironmentContext.Provider value={value}>{children}</EnvironmentContext.Provider>;
}

export function useEnvironment(): EnvironmentContextValue {
  const value = useContext(EnvironmentContext);
  if (!value) throw new Error("useEnvironment must be used inside EnvironmentProvider.");
  return value;
}
