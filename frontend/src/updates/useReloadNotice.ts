import { useEffect, useState } from "react";

import { api } from "../api/client";
import { BUILD_VERSION } from "../buildVersion";
import { reloadTarget } from "./reloadTarget";

const POLL_MS = 30 * 1000;

/** The version YAAP runs now, when it differs from the one this page was built for. Updating restarts YAAP, which
 *  breaks the connection; the first answer after it (or on returning to the tab) is compared with the build. */
export function useReloadNotice(): string | null {
  const [running, setRunning] = useState<string | null>(null);

  useEffect(() => {
    if (!BUILD_VERSION) return;
    let current = true;
    const check = () =>
      void api
        .GET("/api/v1/version")
        .then(({ data }) => {
          if (current && data) setRunning(reloadTarget(BUILD_VERSION, data.version));
        })
        .catch(() => undefined); // YAAP is restarting; the next poll finds it again.
    check();
    const timer = window.setInterval(check, POLL_MS);
    const onVisible = () => {
      if (!document.hidden) check();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      current = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  return running;
}
