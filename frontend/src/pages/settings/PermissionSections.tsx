import { useEffect, useRef, useState } from "react";

import DefaultSkillsSection from "../../components/DefaultSkillsSection";
import PermissionsSection, { type PermissionLevel } from "../../components/PermissionsSection";
import { type LevelSettings, type SettingsLevel, SectionHeader, TAB_OVERRIDE_KEYS } from "./shared";

function permissionLevel(level: SettingsLevel): PermissionLevel {
  return level.kind === "global"
    ? { kind: "global" }
    : { kind: "environment", id: level.environment.id, name: level.environment.name };
}

/**
 * The shared permission components keep their own state and save on their own. To keep the override count in the
 * header current, the level's settings are reloaded shortly after any change inside them.
 */
function useReloadAfterChange(store: LevelSettings): () => void {
  const timer = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );
  return () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void store.reload(), 800);
  };
}

export function PermissionsSettings({ level, store }: { level: SettingsLevel; store: LevelSettings }) {
  const [generation, setGeneration] = useState(0);
  const changed = useReloadAfterChange(store);
  return (
    <>
      <SectionHeader
        level={level}
        store={store}
        title="Permissions"
        overrideKeys={TAB_OVERRIDE_KEYS.permissions}
        onReset={() => setGeneration((value) => value + 1)}
        info={
          level.kind === "global" ? (
            <p>What agents may do without asking. Environments and projects can change it for themselves.</p>
          ) : (
            <p>
              What agents may do without asking in {level.environment.name}. Projects can change it for themselves.
            </p>
          )
        }
      />
      <div className="wb-body wb-body-narrow wb-stack settings-legacy-cards" onChange={changed} onClick={changed}>
        <PermissionsSection key={generation} level={permissionLevel(level)} />
      </div>
    </>
  );
}

export function DefaultSkillsSettings({ level, store }: { level: SettingsLevel; store: LevelSettings }) {
  const [generation, setGeneration] = useState(0);
  const changed = useReloadAfterChange(store);
  return (
    <>
      <SectionHeader
        level={level}
        store={store}
        title="Default skills"
        overrideKeys={TAB_OVERRIDE_KEYS["default-skills"]}
        onReset={() => setGeneration((value) => value + 1)}
        info={
          <p>
            Skills pinned in every new conversation from the first message, for example an answer style.
            {level.kind === "global"
              ? " Environments and projects can set their own list."
              : ` ${level.environment.name} inherits the Global list until you override it here; projects can set their own.`}
          </p>
        }
      />
      <div className="wb-body wb-body-narrow wb-stack settings-legacy-cards" onChange={changed} onClick={changed}>
        <DefaultSkillsSection key={generation} level={permissionLevel(level)} />
      </div>
    </>
  );
}
