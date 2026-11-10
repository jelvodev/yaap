import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { components } from "../api/schema";

export type RunningBuild = components["schemas"]["VersionOut"];

/** The version, commit and channel of the YAAP that answers. */
export function useRunningBuild(): RunningBuild | null {
  const [build, setBuild] = useState<RunningBuild | null>(null);
  useEffect(() => {
    void api.GET("/api/v1/version").then(({ data }) => {
      if (data) setBuild(data);
    });
  }, []);
  return build;
}
