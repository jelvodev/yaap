import AccessSection from "./AccessSection";
import AuditSection from "./AuditSection";
import BackgroundTasksSection from "./BackgroundTasksSection";
import ConnectionsSection from "./ConnectionsSection";
import ContextSection from "./ContextSection";
import FirstStartSection from "./FirstStartSection";
import ModelsSection from "./ModelsSection";
import GitSection from "./GitSection";
import LanguageSection from "./LanguageSection";
import { AppearanceSection, BackupSection, EnvironmentsSection, ProfileSection } from "./LevelSections";
import ModelPolicySection from "./ModelPolicySection";
import { A2aAgentsSection, McpServersSection } from "./OutsideAgentSections";
import { DefaultSkillsSettings, PermissionsSettings } from "./PermissionSections";
import PluginsSection from "./PluginsSection";
import PresetsSection from "./PresetsSection";
import TargetsSection from "./TargetsSection";
import TelemetrySection from "./TelemetrySection";
import UpdatesSection from "./UpdatesSection";
import UsageSection from "./UsageSection";
import UtilityModelsSection from "./UtilityModelsSection";
import VaultSection from "./VaultSection";
import type { LevelSettings, SettingsLevel } from "./shared";

/** One tab of a section, by its slug (sections.ts); parts that exist only in an environment get that level. */
export default function SectionPart({ tab, level, store }: { tab: string; level: SettingsLevel; store: LevelSettings }) {
  const environment = level.kind === "environment" ? level : null;
  switch (tab) {
    case "profile":
      return <ProfileSection level={level} store={store} />;
    case "access":
      return <AccessSection level={level} />;
    case "providers":
      return <ConnectionsSection level={level} store={store} />;
    case "vault":
      return <VaultSection level={level} />;
    case "mcp-servers":
      return environment && <McpServersSection level={environment} />;
    case "a2a-agents":
      return environment && <A2aAgentsSection level={environment} />;
    case "presets":
      return <PresetsSection level={level} store={store} />;
    case "utility":
      return <UtilityModelsSection level={level} store={store} />;
    case "background":
      return <BackgroundTasksSection level={level} store={store} />;
    case "context":
      return <ContextSection level={level} store={store} />;
    case "policy":
      return <ModelPolicySection level={level} store={store} />;
    case "permissions":
      return <PermissionsSettings level={level} store={store} />;
    case "default-skills":
      return <DefaultSkillsSettings level={level} store={store} />;
    case "language":
      return <LanguageSection level={level} store={store} />;
    case "git":
      return <GitSection level={level} store={store} />;
    case "targets":
      return <TargetsSection level={level} />;
    case "budget":
      return <UsageSection level={level} store={store} />;
    case "audit":
      return environment && <AuditSection level={environment} />;
    case "telemetry":
      return <TelemetrySection level={level} store={store} />;
    case "environments":
      return <EnvironmentsSection level={level} />;
    case "appearance":
      return environment && <AppearanceSection level={level} environment={environment.environment} />;
    case "backup":
      return <BackupSection level={level} />;
    case "plugins":
      return <PluginsSection level={level} />;
    case "updates":
      return <UpdatesSection level={level} store={store} />;
    case "models":
      return <ModelsSection level={level} />;
    case "first-start":
      return <FirstStartSection level={level} />;
    default:
      return null;
  }
}
