import { useNavigate } from "react-router-dom";

import ModelOptions, { modelLabel } from "../../components/ModelOptions";
import { Badge, FieldRow, Panel } from "../../components/workbench/Workbench";
import { SearchIndexStatus } from "./StatusFields";
import UtilityTaskModels from "./UtilityTaskModels";
import WorkersPanel from "./WorkersPanel";
import {
  type LevelSettings,
  type SettingsLevel,
  SectionHeader,
  SelectSetting,
  TAB_OVERRIDE_KEYS,
  useModels,
} from "./shared";

const UTILITY_KEY = "utility_model";
const UTILITY_TASKS_KEY = "utility_task_models";
const EMBEDDING_KEY = "embedding_model";

/** Models › Utility and workers: workers for delegated work, the utility model and its tasks, the embedding model. */
export default function UtilityModelsSection({ level, store }: { level: SettingsLevel; store: LevelSettings }) {
  const navigate = useNavigate();
  const environmentId = level.kind === "environment" ? level.environment.id : undefined;
  const models = useModels(environmentId);
  const utility = store.find(UTILITY_KEY);
  const utilityTasks = store.find(UTILITY_TASKS_KEY);
  const embedding = store.findGlobal(EMBEDDING_KEY);
  const choices = models.map((model) => ({ value: model.id, label: modelLabel(model) }));

  return (
    <>
      <SectionHeader
        level={level}
        store={store}
        title="Utility and workers"
        overrideKeys={TAB_OVERRIDE_KEYS.utility}
        info={
          <>
            <p>
              Workers are who delegated work can go to: a preset&apos;s worker by the preset&apos;s name, or Claude Code
              and Codex, which run as their own CLI on your subscription with only YAAP&apos;s tools. Ask the agent for
              one by name; without a name it uses the worker of the conversation&apos;s preset.
            </p>
            <p>
              The utility model does titles, commit messages and other small tasks for a preset whose utility role is
              empty. A small task such as titles can have a model of its own (Utility model per task), which comes
              before both; the check of outside content always stays on the utility model. Embedding is one model for
              the whole installation.
            </p>
          </>
        }
      />
      <div className="wb-body wb-stack settings-models">
        <WorkersPanel level={level} store={store} models={models} />

        <Panel title="Shared roles" flush>
          {utility && (
            <SelectSetting
              level={level}
              setting={utility}
              choices={choices}
              options={<ModelOptions models={models} current={String(utility.value ?? "")} />}
              onSave={(value) => store.save(UTILITY_KEY, value)}
              onReset={() => store.reset(UTILITY_KEY)}
            />
          )}
          {utilityTasks && (
            <UtilityTaskModels
              level={level}
              setting={utilityTasks}
              models={models}
              onSave={(value) => store.save(UTILITY_TASKS_KEY, value)}
              onReset={() => store.reset(UTILITY_TASKS_KEY)}
            />
          )}
          {embedding &&
            (level.kind === "global" ? (
              <SelectSetting
                level={level}
                setting={embedding}
                choices={embedding.options}
                onSave={(value) => store.save(EMBEDDING_KEY, value)}
                onReset={() => store.reset(EMBEDDING_KEY)}
                extra={<SearchIndexStatus setting={String(embedding.value)} />}
              />
            ) : (
              <FieldRow label={embedding.label} hint="Global only">
                <span>
                  {embedding.options.find((option) => option.value === embedding.value)?.label ?? String(embedding.value)}
                </span>
                <Badge tone="level">from Global</Badge>
                <button
                  type="button"
                  className="button button-ghost"
                  onClick={() => navigate("/settings/models/utility?level=global")}
                >
                  Change at Global
                </button>
                <p className="settings-help">{embedding.description}</p>
              </FieldRow>
            ))}
        </Panel>
      </div>
    </>
  );
}
