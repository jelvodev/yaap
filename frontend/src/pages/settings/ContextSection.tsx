import { Panel } from "../../components/workbench/Workbench";
import { CONTEXT_KEYS, contextChoices } from "../../components/contextSettings";
import { type LevelSettings, type SettingsLevel, SectionHeader, SelectSetting, TAB_OVERRIDE_KEYS } from "./shared";

/** How large conversations may grow and when YAAP compacts them; a project can override each. */
export default function ContextSection({ level, store }: { level: SettingsLevel; store: LevelSettings }) {
  const settings = CONTEXT_KEYS.map((key) => store.find(key)).filter((setting) => setting !== undefined);
  return (
    <>
      <SectionHeader
        level={level}
        store={store}
        title="Context"
        overrideKeys={TAB_OVERRIDE_KEYS.context}
        info={
          <p>
            How large a conversation&apos;s context may grow, and when YAAP compacts it: first it shortens old tool
            output, then it summarises the earlier messages, each at a share of the limit, so long conversations stay
            cheaper. The model keeps its full window. A conversation can choose its own limit next to its effort.
            Claude Code and Codex compact their own sessions. The repository map starts each project conversation
            with the project&apos;s folders, main modules and public symbols, made once per commit.
          </p>
        }
      />
      <div className="wb-body wb-body-narrow wb-stack">
        <Panel title="Context" flush>
          {settings.map((setting) => {
            const choices = contextChoices(setting.key, setting.value);
            return (
              <SelectSetting
                key={setting.key}
                level={level}
                setting={setting}
                choices={choices}
                onSave={(value) =>
                  store.save(setting.key, choices.find((choice) => choice.value === value)?.stored ?? value)
                }
                onReset={() => store.reset(setting.key)}
              />
            );
          })}
        </Panel>
      </div>
    </>
  );
}
