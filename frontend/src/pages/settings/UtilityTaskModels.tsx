import { useState } from "react";

import type { ModelInfo, Setting } from "../../api/client";
import ModelOptions from "../../components/ModelOptions";
import { type SettingsLevel, SettingField } from "./shared";

type TaskModels = Record<string, string>;

/** A stored or inherited value as task → model; anything else counts as no models. */
function taskModels(value: unknown): TaskModels {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value).filter((entry): entry is [string, string] => typeof entry[1] === "string"),
  );
}

/**
 * The model per utility task (title, commit message and the like); a task without one uses the utility model. The
 * setting merges per task over the levels, so a level stores only the tasks it sets; choosing the utility model where a
 * higher level names one stores an empty value, which means the utility model.
 */
export default function UtilityTaskModels({
  level,
  setting,
  models,
  onSave,
  onReset,
}: {
  level: SettingsLevel;
  setting: Setting;
  models: ModelInfo[];
  onSave: (value: TaskModels) => Promise<string | null>;
  onReset: () => Promise<string | null>;
}) {
  const [message, setMessage] = useState<string | null>(null);
  const current = taskModels(setting.value);
  const own = taskModels(setting.level_value);
  const inherited = taskModels(setting.inherited_value);
  const label = (task: string) => setting.options.find((option) => option.value === task)?.label ?? task;
  const modelName = (id: string) => models.find((model) => model.id === id)?.display_name ?? id;
  const display = (value: unknown) =>
    Object.entries(taskModels(value))
      .filter(([, model]) => model)
      .map(([task, model]) => `${label(task)}: ${modelName(model)}`)
      .join(", ");

  const choose = (task: string, model: string) => {
    const next = { ...own };
    if (model || inherited[task]) next[task] = model;
    else delete next[task];
    const saved = Object.keys(next).length ? onSave(next) : onReset();
    void saved.then(setMessage);
  };

  return (
    <SettingField
      level={level}
      setting={setting}
      display={display}
      message={message}
      onReset={() => void onReset().then(setMessage)}
      control={
        <div className="settings-task-models">
          {setting.options.map((option) => (
            <label key={option.value} className="settings-task-model">
              <span>{option.label}</span>
              <select value={current[option.value] ?? ""} onChange={(event) => choose(option.value, event.target.value)}>
                <option value="">Utility model</option>
                <ModelOptions models={models} current={current[option.value] ?? ""} />
              </select>
            </label>
          ))}
        </div>
      }
    />
  );
}
