// Tests of the Settings sections and their redirects (npm run test:unit). Node strips the types of sections.ts as it
// loads it.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  MOVED_SECTIONS,
  SECTION_OVERRIDE_KEYS,
  SETTINGS_SECTIONS,
  pathAtLevel,
  resolveSettingsRoute,
  sectionTitle,
  sectionsAt,
  tabsAt,
} from "./sections.ts";

const OLD_ADDRESSES = [
  // old slug, level, where it goes
  ["profile", "global", "/settings/account/profile"],
  ["access", "global", "/settings/account/access"],
  ["vault", "global", "/settings/connections/vault"],
  ["vault", "environment", "/settings/connections/vault"],
  ["context", "global", "/settings/models/context"],
  ["context", "environment", "/settings/models/context"],
  ["model-policy", "environment", "/settings/models/policy"],
  ["language", "environment", "/settings/agent/language"],
  ["permissions", "global", "/settings/agent/permissions"],
  ["default-skills", "environment", "/settings/agent/default-skills"],
  ["audit", "environment", "/settings/usage/audit"],
  ["telemetry", "global", "/settings/usage/telemetry"],
  ["appearance", "environment", "/settings/environments/appearance"],
  ["backup", "global", "/settings/system/backup"],
  ["plugins", "global", "/settings/system/plugins"],
  // An old section the level does not have goes to the level's first section, as before.
  ["profile", "environment", "/settings/connections"],
  ["model-policy", "global", "/settings/account"],
  ["audit", "global", "/settings/account"],
  ["appearance", "global", "/settings/account"],
  ["backup", "environment", "/settings/connections"],
  ["unknown", "global", "/settings/account"],
];

for (const [slug, level, expected] of OLD_ADDRESSES) {
  test(`the old address /settings/${slug} at ${level} redirects to ${expected}`, () => {
    assert.deepEqual(resolveSettingsRoute(slug, undefined, level), { kind: "redirect", path: expected });
  });
}

test("every old section is mapped to a section and tab that exist", () => {
  for (const [slug, target] of Object.entries(MOVED_SECTIONS)) {
    const section = SETTINGS_SECTIONS.find((item) => item.slug === target.section);
    assert.ok(section, `${slug} names a section`);
    assert.ok(!target.tab || section.tabs.some((item) => item.slug === target.tab), `${slug} names a tab`);
    assert.ok(!SETTINGS_SECTIONS.some((item) => item.slug === slug), `${slug} is not also a current section`);
  }
});

test("every section and tab address resolves to itself at each level that has it", () => {
  for (const level of ["global", "environment"]) {
    for (const section of sectionsAt(level)) {
      const tabs = tabsAt(section, level);
      const shown = resolveSettingsRoute(section.slug, undefined, level);
      assert.equal(shown.kind, "show");
      assert.equal(shown.tab.slug, tabs[0].slug);
      for (const item of tabs) {
        const route = resolveSettingsRoute(section.slug, item.slug, level);
        assert.equal(route.kind, "show", `${section.slug}/${item.slug} at ${level}`);
        assert.equal(route.section.slug, section.slug);
        assert.equal(route.tab.slug, item.slug);
      }
    }
  }
});

test("a tab the level does not have goes to its section", () => {
  assert.deepEqual(resolveSettingsRoute("models", "policy", "global"), { kind: "redirect", path: "/settings/models" });
  assert.deepEqual(resolveSettingsRoute("models", "nothing", "environment"), {
    kind: "redirect",
    path: "/settings/models",
  });
});

test("a section the level does not have goes to the level's first section", () => {
  assert.deepEqual(resolveSettingsRoute("targets", undefined, "global"), {
    kind: "redirect",
    path: "/settings/account",
  });
  assert.deepEqual(resolveSettingsRoute("system", "backup", "environment"), {
    kind: "redirect",
    path: "/settings/connections",
  });
});

test("Global has eight sections and an environment seven, the last titled Environment", () => {
  assert.deepEqual(
    sectionsAt("global").map((section) => section.slug),
    ["account", "connections", "models", "agent", "git", "usage", "environments", "system"],
  );
  const inEnvironment = sectionsAt("environment");
  assert.deepEqual(
    inEnvironment.map((section) => section.slug),
    ["connections", "models", "agent", "git", "targets", "usage", "environments"],
  );
  assert.equal(sectionTitle(inEnvironment.at(-1), "environment"), "Environment");
  assert.equal(sectionTitle(inEnvironment.at(-1), "global"), "Environments");
});

test("tab slugs are unique across sections", () => {
  const slugs = SETTINGS_SECTIONS.flatMap((section) => section.tabs.map((item) => item.slug));
  assert.equal(new Set(slugs).size, slugs.length);
});

test("a section's override keys are the union of its former sections' keys", () => {
  assert.deepEqual(SECTION_OVERRIDE_KEYS.models, [
    "utility_model",
    "utility_task_models",
    "claude_code_worker",
    "codex_worker",
    "utility_tasks_off",
    "context_limit",
    "auto_compact",
    "compact_at",
    "trim_at",
    "repo_map",
    "context_savings",
    "model_policy",
  ]);
  assert.deepEqual(SECTION_OVERRIDE_KEYS.agent, [
    "tool_permissions",
    "injection_check",
    "command_allowlist",
    "web_allowlist",
    "default_skills",
    "reply_language",
    "work_language",
  ]);
  assert.deepEqual(SECTION_OVERRIDE_KEYS.usage, ["otlp_endpoint", "otlp_headers", "otlp_include_content"]);
  assert.deepEqual(SECTION_OVERRIDE_KEYS.connections, ["anthropic_connection", "openai_connection"]);
});

test("switching the level keeps the tab when the new level has it", () => {
  assert.equal(pathAtLevel("models", "context", "global"), "/settings/models/context");
  assert.equal(pathAtLevel("models", "policy", "global"), "/settings/models");
  assert.equal(pathAtLevel("usage", "audit", "global"), "/settings/usage");
  assert.equal(pathAtLevel("system", "plugins", "environment"), "/settings/connections");
  assert.equal(pathAtLevel("environments", "environments", "environment"), "/settings/environments");
});
