// The sections of the Settings page, their tabs, and where each address leads. Pure, so the redirects are tested
// without a browser (sections.test.mjs); it imports nothing.

export type LevelKind = "global" | "environment";

/** A part of a section, shown as a tab when the section has more than one part at a level. */
export interface SettingsTab {
  slug: string;
  title: string;
  levels: readonly LevelKind[];
  /** The settings in this part that an environment can override. */
  overrideKeys: readonly string[];
}

export interface SettingsSection {
  slug: string;
  title: string;
  /** The title in an environment, where it differs from the one at Global. */
  environmentTitle?: string;
  tabs: readonly SettingsTab[];
}

const BOTH: readonly LevelKind[] = ["global", "environment"];
const GLOBAL: readonly LevelKind[] = ["global"];
const ENVIRONMENT: readonly LevelKind[] = ["environment"];

function tab(
  slug: string,
  title: string,
  levels: readonly LevelKind[],
  overrideKeys: readonly string[] = [],
): SettingsTab {
  return { slug, title, levels, overrideKeys };
}

/** The sections in rail order; a section exists at a level when one of its tabs does. Tab slugs are unique. */
export const SETTINGS_SECTIONS: readonly SettingsSection[] = [
  {
    slug: "account",
    title: "Account",
    tabs: [tab("profile", "Profile", GLOBAL), tab("access", "Access", GLOBAL)],
  },
  {
    slug: "connections",
    title: "Connections",
    tabs: [
      tab("providers", "Providers", BOTH, ["anthropic_connection", "openai_connection"]),
      tab("vault", "Vault", BOTH),
      tab("mcp-servers", "MCP servers", ENVIRONMENT),
      tab("a2a-agents", "A2A agents", ENVIRONMENT),
    ],
  },
  {
    slug: "models",
    title: "Models",
    tabs: [
      tab("presets", "Presets", BOTH),
      tab("utility", "Utility and workers", BOTH, [
        "utility_model",
        "utility_task_models",
        "claude_code_worker",
        "codex_worker",
      ]),
      tab("context", "Context", BOTH, ["context_limit", "auto_compact", "compact_at", "trim_at", "repo_map"]),
      tab("policy", "Policy", ENVIRONMENT, ["model_policy"]),
    ],
  },
  {
    slug: "agent",
    title: "Agent",
    tabs: [
      tab("permissions", "Permissions", BOTH, [
        "tool_permissions",
        "injection_check",
        "command_allowlist",
        "web_allowlist",
      ]),
      tab("default-skills", "Default skills", BOTH, ["default_skills"]),
      tab("language", "Language", BOTH, ["reply_language", "work_language"]),
    ],
  },
  {
    slug: "git",
    title: "Git and GitHub",
    tabs: [tab("git", "Git and GitHub", BOTH, ["git_author_name", "git_author_email"])],
  },
  { slug: "targets", title: "Targets", tabs: [tab("targets", "Targets", ENVIRONMENT)] },
  {
    slug: "usage",
    title: "Usage and audit",
    tabs: [
      tab("budget", "Usage and budget", BOTH),
      tab("audit", "Audit log", ENVIRONMENT),
      tab("telemetry", "Telemetry", BOTH, ["otlp_endpoint", "otlp_headers", "otlp_include_content"]),
    ],
  },
  {
    slug: "environments",
    title: "Environments",
    environmentTitle: "Environment",
    tabs: [tab("environments", "Environments", GLOBAL), tab("appearance", "Appearance", ENVIRONMENT)],
  },
  {
    slug: "system",
    title: "System",
    tabs: [
      tab("backup", "Backup", GLOBAL),
      tab("plugins", "Plugins", GLOBAL),
      tab("updates", "Updates", GLOBAL),
      tab("first-start", "First start", GLOBAL),
    ],
  },
];

/** Older section addresses and where each went; the slugs that stayed are not listed. */
export const MOVED_SECTIONS: Readonly<Record<string, { section: string; tab?: string }>> = {
  profile: { section: "account", tab: "profile" },
  access: { section: "account", tab: "access" },
  vault: { section: "connections", tab: "vault" },
  context: { section: "models", tab: "context" },
  "model-policy": { section: "models", tab: "policy" },
  language: { section: "agent", tab: "language" },
  permissions: { section: "agent", tab: "permissions" },
  "default-skills": { section: "agent", tab: "default-skills" },
  audit: { section: "usage", tab: "audit" },
  telemetry: { section: "usage", tab: "telemetry" },
  appearance: { section: "environments", tab: "appearance" },
  backup: { section: "system", tab: "backup" },
  plugins: { section: "system", tab: "plugins" },
};

/** The settings each section holds that an environment can override: the union of its tabs. */
export const SECTION_OVERRIDE_KEYS: Readonly<Record<string, readonly string[]>> = Object.fromEntries(
  SETTINGS_SECTIONS.map((section) => [section.slug, section.tabs.flatMap((item) => item.overrideKeys)]),
);

/** The settings each tab holds that an environment can override, by tab slug. */
export const TAB_OVERRIDE_KEYS: Readonly<Record<string, readonly string[]>> = Object.fromEntries(
  SETTINGS_SECTIONS.flatMap((section) => section.tabs.map((item) => [item.slug, item.overrideKeys])),
);

export function tabsAt(section: SettingsSection, level: LevelKind): SettingsTab[] {
  return section.tabs.filter((item) => item.levels.includes(level));
}

export function sectionsAt(level: LevelKind): SettingsSection[] {
  return SETTINGS_SECTIONS.filter((section) => tabsAt(section, level).length > 0);
}

export function sectionTitle(section: SettingsSection, level: LevelKind): string {
  return level === "environment" ? (section.environmentTitle ?? section.title) : section.title;
}

/** The address of a section, or of one of its tabs; the level's query string is added by the caller. */
export function settingsPath(section: string, tab?: string): string {
  return tab ? `/settings/${section}/${tab}` : `/settings/${section}`;
}

export type SettingsRoute =
  | { kind: "show"; section: SettingsSection; tabs: SettingsTab[]; tab: SettingsTab }
  | { kind: "redirect"; path: string };

function firstSectionPath(level: LevelKind): string {
  return settingsPath(sectionsAt(level)[0].slug);
}

/**
 * What `/settings/<section>/<tab>` shows at a level. An old section redirects to its new place; a section or old
 * section the level does not have goes to the level's first section; an unknown tab goes to its section.
 */
export function resolveSettingsRoute(
  sectionSlug: string | undefined,
  tabSlug: string | undefined,
  level: LevelKind,
): SettingsRoute {
  const sections = sectionsAt(level);
  const section = sectionSlug ? sections.find((item) => item.slug === sectionSlug) : sections[0];
  if (section) {
    const tabs = tabsAt(section, level);
    const current = tabSlug ? tabs.find((item) => item.slug === tabSlug) : tabs[0];
    if (current) return { kind: "show", section, tabs, tab: current };
    return { kind: "redirect", path: settingsPath(section.slug) };
  }
  const moved = sectionSlug ? MOVED_SECTIONS[sectionSlug] : undefined;
  if (moved) {
    const target = sections.find((item) => item.slug === moved.section);
    const available = target && (!moved.tab || tabsAt(target, level).some((item) => item.slug === moved.tab));
    if (available) return { kind: "redirect", path: settingsPath(moved.section, moved.tab) };
  }
  return { kind: "redirect", path: firstSectionPath(level) };
}

/** Where to go when the level changes: the same tab if the new level has it, else its section, else the first. */
export function pathAtLevel(sectionSlug: string, tabSlug: string | undefined, level: LevelKind): string {
  const section = sectionsAt(level).find((item) => item.slug === sectionSlug);
  if (!section) return firstSectionPath(level);
  const tabs = tabsAt(section, level);
  return tabSlug && tabs.some((item) => item.slug === tabSlug) && tabs.length > 1
    ? settingsPath(section.slug, tabSlug)
    : settingsPath(section.slug);
}
