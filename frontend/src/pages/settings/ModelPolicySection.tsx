import ModelPolicyPanel from "../../components/ModelPolicyPanel";
import { type LevelSettings, type SettingsLevel, SectionHeader, TAB_OVERRIDE_KEYS } from "./shared";

/** The environment's model policy; projects narrow it in their own settings. */
export default function ModelPolicySection({ level, store }: { level: SettingsLevel; store: LevelSettings }) {
  if (level.kind !== "environment") return null;
  const environment = level.environment;
  return (
    <>
      <SectionHeader
        level={level}
        store={store}
        title="Policy"
        overrideKeys={TAB_OVERRIDE_KEYS.policy}
        info={
          <p>
            Which connections, models and A2A agents {environment.name} may use, and what may never leave YAAP. Every
            model request is checked: conversations, delegated tasks, Claude Code and Codex as workers, and the utility
            model. Projects can only narrow it.
          </p>
        }
      />
      <div className="wb-body wb-body-narrow wb-stack settings-legacy-cards">
        <ModelPolicyPanel
          key={environment.id}
          level={{ kind: "environment", environmentId: environment.id, name: environment.name }}
        />
      </div>
    </>
  );
}
