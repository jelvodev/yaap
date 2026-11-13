import { ListCard, type PermissionLevel, scopeOf } from "./PermissionsSection";
import { useScopedSettings } from "../pages/settings/useScopedSettings";

/** The skills every new conversation starts with pinned, at one level. */
export default function DefaultSkillsSection({ level }: { level: PermissionLevel }) {
  const { find, set, reset } = useScopedSettings(scopeOf(level));
  const setting = find("default_skills");

  if (!setting) return null;
  return (
    <ListCard
      setting={setting}
      level={level}
      placeholder="caveman"
      onSave={(lines) => set(setting.key, lines)}
      onReset={() => reset(setting.key)}
    />
  );
}
