import { Panel } from "../../components/workbench/Workbench";
import { type LevelSettings, type SettingsLevel, SectionHeader, TAB_OVERRIDE_KEYS, TextSetting } from "./shared";

const PLACEHOLDERS: Record<string, string> = {
  reply_language: "Empty: the language of your message",
  work_language: "English",
};

/** The reply and work language, at Global or in an environment; a project can override both in its settings. */
export default function LanguageSection({ level, store }: { level: SettingsLevel; store: LevelSettings }) {
  const settings = store.settings.filter((setting) => setting.key in PLACEHOLDERS);
  return (
    <>
      <SectionHeader
        level={level}
        store={store}
        title="Language"
        overrideKeys={TAB_OVERRIDE_KEYS.language}
        info={
          <p>
            The language YAAP replies in, and the language of what it writes as work: code comments, documentation,
            commit messages, pull requests, project memory and rules. The interface itself stays English.
          </p>
        }
      />
      <div className="wb-body wb-body-narrow wb-stack">
        <Panel title="Language" flush>
          {settings.map((setting) => (
            <TextSetting
              key={setting.key}
              level={level}
              setting={setting}
              placeholder={PLACEHOLDERS[setting.key]}
              maxLength={40}
              onSave={(value) => store.save(setting.key, value)}
              onReset={() => store.reset(setting.key)}
            />
          ))}
        </Panel>
      </div>
    </>
  );
}
