import { useEffect, useState } from "react";

import { api } from "../api/client";
import { notifyIfHidden } from "../notifications";
import { markNotified, notifiedVersion, onSeenChanged, seenVersion } from "./seenVersion";

// The check runs once a day; looking once a minute shows its result soon enough.
const POLL_MS = 60 * 1000;

/** Whether Settings shows a dot (a newer version exists and its section was not opened yet). Also gives the one
 *  notification per new version. */
export function useUpdateNotice(): { dot: boolean } {
  const [newest, setNewest] = useState<string | null>(null);
  const [seen, setSeen] = useState<string | null>(seenVersion());

  useEffect(() => onSeenChanged(() => setSeen(seenVersion())), []);

  useEffect(() => {
    let current = true;
    const check = () =>
      void api.GET("/api/v1/updates").then(({ data }) => {
        if (!current || !data) return;
        const version = data.update_available ? data.newest_version : null;
        setNewest(version);
        if (version && data.announced_version === version && notifiedVersion() !== version) {
          markNotified(version);
          const body = `Version ${version}. See Settings › System › Updates.`;
          notifyIfHidden("A new YAAP is available", body, `update-${version}`);
        }
      });
    check();
    const timer = window.setInterval(check, POLL_MS);
    return () => {
      current = false;
      window.clearInterval(timer);
    };
  }, []);

  return { dot: newest !== null && newest !== seen };
}
