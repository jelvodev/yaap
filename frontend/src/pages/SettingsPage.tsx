import { Navigate, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { type Level, LevelSwitch } from "../components/workbench/LevelPicker";
import { RailHeading, RailItem, Workbench } from "../components/workbench/Workbench";
import { useEnvironment } from "../environment/EnvironmentProvider";
import SectionPart from "./settings/SectionPart";
import SectionTabs from "./settings/SectionTabs";
import { SectionFrameContext } from "./settings/sectionFrame";
import { pathAtLevel, resolveSettingsRoute, sectionTitle, sectionsAt, settingsPath } from "./settings/sections";
import {
  type SettingsLevel,
  SECTION_OVERRIDE_KEYS,
  levelSearch,
  overriddenKeys,
  useLevelSettings,
} from "./settings/shared";
import "./settings/settings.css";

/**
 * Settings as a workbench: the level on top of the rail, the sections below it, the section's detail beside them.
 * The address is /settings/<section>/<tab>; sections.ts holds the sections, their tabs and the old addresses.
 */
export default function SettingsPage() {
  const { environments, active } = useEnvironment();
  const { section: sectionSlug, tab: tabSlug } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();

  // The level lives in the URL: ?level=global or ?environment=<id>; without either, the active environment.
  const requestedId = Number(params.get("environment"));
  const requested = environments.find((environment) => environment.id === requestedId);
  const environment = params.get("level") === "global" ? undefined : (requested ?? active ?? undefined);
  const level: SettingsLevel = environment ? { kind: "environment", environment } : { kind: "global" };
  const store = useLevelSettings(level);

  const route = resolveSettingsRoute(sectionSlug, tabSlug, level.kind);
  if (route.kind === "redirect") return <Navigate to={`${route.path}${levelSearch(level)}`} replace />;
  const { section, tabs, tab } = route;

  function go(path: string, nextLevel: SettingsLevel = level) {
    navigate(`${path}${levelSearch(nextLevel)}`);
  }

  function changeLevel(next: Level) {
    const target = next.kind === "environment" ? environments.find((item) => item.id === next.environmentId) : undefined;
    const nextLevel: SettingsLevel = target ? { kind: "environment", environment: target } : { kind: "global" };
    go(pathAtLevel(section.slug, tab.slug, nextLevel.kind), nextLevel);
  }

  const switchLevel: Level =
    level.kind === "global" ? { kind: "global" } : { kind: "environment", environmentId: level.environment.id };

  const rail = (
    <>
      <RailHeading>Level</RailHeading>
      <LevelSwitch level={switchLevel} onChange={changeLevel} />
      <RailHeading>Section</RailHeading>
      {sectionsAt(level.kind).map((item) => {
        const overridden = overriddenKeys(level, store, SECTION_OVERRIDE_KEYS[item.slug] ?? []).length;
        return (
          <RailItem
            key={item.slug}
            active={item.slug === section.slug}
            onClick={() => go(settingsPath(item.slug))}
            count={overridden > 0 ? overridden : undefined}
            title={overridden > 0 ? `${overridden} overridden in ${level.kind === "environment" ? level.environment.name : ""}` : undefined}
          >
            {sectionTitle(item, level.kind)}
          </RailItem>
        );
      })}
    </>
  );

  const title = sectionTitle(section, level.kind);
  const tabBar =
    tabs.length > 1 ? (
      <SectionTabs
        section={title}
        tabs={tabs}
        current={tab.slug}
        level={level}
        store={store}
        onChange={(next) => go(settingsPath(section.slug, next))}
      />
    ) : null;

  // Parts remount per level, so their own state never leaks from one level into another.
  const key = `${tab.slug}-${level.kind === "global" ? "global" : level.environment.id}`;
  return (
    <Workbench label="Settings" rail={rail}>
      <SectionFrameContext.Provider value={{ title, tabs: tabBar }}>
        <SectionPart key={key} tab={tab.slug} level={level} store={store} />
      </SectionFrameContext.Provider>
    </Workbench>
  );
}
