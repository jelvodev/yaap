import { useCallback, useEffect, useState } from "react";

import type { Setting } from "../api/client";
import { ListCard, type PermissionLevel, settingsUrl } from "./PermissionsSection";

/** The skills every new conversation starts with pinned, at one level. */
export default function DefaultSkillsSection({ level }: { level: PermissionLevel }) {
  const [setting, setSetting] = useState<Setting | null>(null);

  const load = useCallback(async () => {
    const list = (await fetch(settingsUrl(level)).then((response) => response.json())) as Setting[];
    setSetting(list.find((item) => item.key === "default_skills") ?? null);
  }, [level.kind, level.kind === "global" ? 0 : level.id]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!setting) return null;
  return <ListCard setting={setting} level={level} placeholder="caveman" onChange={setSetting} />;
}
