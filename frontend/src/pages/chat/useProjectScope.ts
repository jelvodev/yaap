import { useCallback, useEffect, useState } from "react";

import type { Project } from "../../api/client";
import { readScope, writeScope } from "../../environment/scope";

export interface ProjectScope {
  /** The project the sidebar shows, or null for conversations outside a project. */
  scope: number | null;
  /** Choose a project; the choice is remembered for the environment. */
  choose: (projectId: number | null) => void;
  /** Show a project without remembering it, as opening one of its conversations does. */
  show: (projectId: number | null) => void;
}

/** The project chosen in the sidebar, remembered per environment in the browser. */
export function useProjectScope(environmentId: number, projects: Project[], loaded: boolean): ProjectScope {
  const [scope, setScope] = useState<number | null>(() => readScope(environmentId));

  // Once the projects are known, a remembered project that no longer exists falls back to no project.
  useEffect(() => {
    if (!loaded) return;
    setScope((current) => (current !== null && projects.some((project) => project.id === current) ? current : null));
  }, [loaded]);

  const choose = useCallback(
    (projectId: number | null) => {
      setScope(projectId);
      writeScope(environmentId, projectId);
    },
    [environmentId],
  );

  return { scope, choose, show: setScope };
}
