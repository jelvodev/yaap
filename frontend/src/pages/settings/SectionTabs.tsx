import { Badge, Tabs } from "../../components/workbench/Workbench";
import type { SettingsTab } from "./sections";
import { type LevelSettings, type SettingsLevel, levelTitle, overriddenKeys } from "./shared";

/** The tabs of a section, each with the number of values the environment overrides in it. */
export default function SectionTabs({
  section,
  tabs,
  current,
  level,
  store,
  onChange,
}: {
  section: string;
  tabs: SettingsTab[];
  current: string;
  level: SettingsLevel;
  store: LevelSettings;
  onChange: (tab: string) => void;
}) {
  return (
    <Tabs
      label={section}
      value={current}
      onChange={onChange}
      tabs={tabs.map((tab) => {
        const overridden = overriddenKeys(level, store, tab.overrideKeys).length;
        return {
          value: tab.slug,
          label: (
            <>
              {tab.title}
              {overridden > 0 && (
                <>
                  {" "}
                  <Badge tone="over" title={`${overridden} overridden in ${levelTitle(level)}`}>
                    {overridden}
                  </Badge>
                </>
              )}
            </>
          ),
        };
      })}
    />
  );
}
