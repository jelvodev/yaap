import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { type ModelInfo, type Preset, api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";
import ModelOptions from "../../components/ModelOptions";
import { Badge, EmptyState, Panel } from "../../components/workbench/Workbench";
import PresetRouting from "./PresetRouting";
import { type LevelSettings, type SettingsLevel, SectionHeader, SelectSetting, useModels } from "./shared";

const PLAIN_CHAT_KEY = "plain_chat_preset";

const WORKER_HINT = "Delegated work. Fallback: the default preset's worker, else this preset's main model.";
const UTILITY_HINT = "Titles, commit messages and other small tasks. Fallback: the Utility model setting.";

function UnavailableBadge({ models, id }: { models: ModelInfo[]; id: string | null | undefined }) {
  const model = models.find((item) => item.id === id);
  if (!model || model.available) return null;
  return (
    <Badge tone="warn" title={model.unavailable_reason ?? undefined}>
      no key
    </Badge>
  );
}

/** A role's model and effort in a preset; the empty choice means the role falls back. */
function RoleSelect({
  label,
  hint,
  id,
  models,
  model,
  effort,
  onChange,
}: {
  label: string;
  hint: string;
  id: string;
  models: ModelInfo[];
  model: string | null;
  effort: string | null;
  onChange: (model: string | null, effort: string | null) => void;
}) {
  const info = models.find((item) => item.id === model);
  return (
    <div className="settings-role-cell" title={hint}>
      <label className="visually-hidden" htmlFor={id}>
        {label}
      </label>
      <select
        id={id}
        value={model ?? ""}
        onChange={(event) => {
          const next = models.find((item) => item.id === event.target.value);
          onChange(next ? next.id : null, next ? (next.efforts.includes(effort ?? "") ? effort : next.default_effort) : null);
        }}
      >
        <option value="">Fallback</option>
        <ModelOptions models={models} current={model ?? ""} />
      </select>
      {info && (
        <select
          aria-label={`${label} effort`}
          value={effort ?? info.default_effort}
          onChange={(event) => onChange(info.id, event.target.value)}
        >
          {info.efforts.map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </select>
      )}
      <UnavailableBadge models={models} id={model} />
    </div>
  );
}

const REPLY_HINT = "The longest reply in tokens. Empty: the model's maximum.";

/** The preset's reply length, saved when the field is left; empty is the model's maximum. */
function ReplyLength({
  preset,
  model,
  onSave,
}: {
  preset: Preset;
  model: ModelInfo | undefined;
  onSave: (value: number | null) => void;
}) {
  const stored = preset.max_output_tokens ? String(preset.max_output_tokens) : "";
  const [value, setValue] = useState(stored);
  useEffect(() => setValue(stored), [stored]);
  const maximum = model?.max_output_tokens;
  return (
    <div className="settings-role-cell" title={REPLY_HINT}>
      <label className="visually-hidden" htmlFor={`preset-reply-${preset.id}`}>
        Longest reply
      </label>
      <input
        id={`preset-reply-${preset.id}`}
        className="settings-preset-reply"
        type="number"
        min={1024}
        max={maximum}
        step={1024}
        placeholder={maximum ? `max ${maximum.toLocaleString()}` : "model maximum"}
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onBlur={() => {
          if (value.trim() !== stored) onSave(value.trim() ? Number(value) : null);
        }}
      />
    </div>
  );
}

function PresetRow({
  preset,
  models,
  onChanged,
}: {
  preset: Preset;
  models: ModelInfo[];
  onChanged: () => Promise<void>;
}) {
  const [name, setName] = useState(preset.name);
  const [message, setMessage] = useState<string | null>(null);
  const model = models.find((item) => item.id === preset.model);

  useEffect(() => setName(preset.name), [preset.name]);

  async function save(body: components["schemas"]["PresetUpdate"]) {
    const { error } = await api.PATCH("/api/v1/presets/{preset_id}", {
      params: { path: { preset_id: preset.id } },
      body,
    });
    setMessage(error ? errorMessage(error) : null);
    await onChanged();
  }

  async function remove() {
    if (!window.confirm(`Remove the preset ${preset.name}?`)) return;
    const { error } = await api.DELETE("/api/v1/presets/{preset_id}", { params: { path: { preset_id: preset.id } } });
    setMessage(error ? errorMessage(error) : null);
    await onChanged();
  }

  return (
    <>
      <tr>
        <td>
          <label className="settings-default" title="Selected for new conversations">
            <input
              type="radio"
              name="default-preset"
              checked={preset.is_default}
              onChange={() => void save({ is_default: true })}
            />
            <span className="visually-hidden">Default preset</span>
            <span aria-hidden="true">{preset.is_default ? "★" : "☆"}</span>
          </label>
        </td>
        <td>
          <label className="visually-hidden" htmlFor={`preset-name-${preset.id}`}>
            Name
          </label>
          <input
            id={`preset-name-${preset.id}`}
            className="settings-preset-name"
            value={name}
            maxLength={40}
            onChange={(event) => setName(event.target.value)}
            onBlur={() => {
              if (name.trim() && name.trim() !== preset.name) void save({ name: name.trim() });
            }}
          />
        </td>
        <td>
          <div className="settings-role-cell">
            <label className="visually-hidden" htmlFor={`preset-model-${preset.id}`}>
              Main model
            </label>
            <select
              id={`preset-model-${preset.id}`}
              value={preset.model}
              onChange={(event) => {
                const next = models.find((item) => item.id === event.target.value);
                const effort = next?.efforts.includes(preset.effort) ? preset.effort : next?.default_effort;
                void save({ model: event.target.value, effort });
              }}
            >
              <ModelOptions models={models} current={preset.model} />
            </select>
            <label className="visually-hidden" htmlFor={`preset-effort-${preset.id}`}>
              Effort
            </label>
            <select
              id={`preset-effort-${preset.id}`}
              value={preset.effort}
              onChange={(event) => void save({ effort: event.target.value })}
            >
              {(model?.efforts ?? [preset.effort]).map((effort) => (
                <option key={effort} value={effort}>
                  {effort}
                </option>
              ))}
            </select>
            <UnavailableBadge models={models} id={preset.model} />
          </div>
        </td>
        <td>
          <RoleSelect
            label="Worker"
            hint={WORKER_HINT}
            id={`preset-worker-${preset.id}`}
            models={models}
            model={preset.worker_model ?? null}
            effort={preset.worker_effort ?? null}
            onChange={(next, effort) => void save({ worker_model: next, worker_effort: effort })}
          />
        </td>
        <td>
          <RoleSelect
            label="Utility"
            hint={UTILITY_HINT}
            id={`preset-utility-${preset.id}`}
            models={models}
            model={preset.utility_model ?? null}
            effort={preset.utility_effort ?? null}
            onChange={(next, effort) => void save({ utility_model: next, utility_effort: effort })}
          />
        </td>
        <td>
          <ReplyLength preset={preset} model={model} onSave={(value) => void save({ max_output_tokens: value })} />
        </td>
        <td className="wb-num">
          <button type="button" className="button button-ghost" onClick={() => void remove()}>
            Remove
          </button>
        </td>
      </tr>
      <PresetRouting preset={preset} models={models} onSave={(body) => void save(body)} />
      {message && (
        <tr>
          <td colSpan={7} className="form-error">
            {message}
          </td>
        </tr>
      )}
    </>
  );
}

/** Which preset conversations outside a project start with; empty is the default preset. */
function PlainChatPreset({ level, store, presets }: { level: SettingsLevel; store: LevelSettings; presets: Preset[] }) {
  const setting = store.find(PLAIN_CHAT_KEY);
  if (!setting) return null;
  const choices = [
    { value: "", label: "The default preset" },
    ...presets.map((preset) => ({ value: preset.name, label: preset.name })),
  ];
  return (
    <SelectSetting
      level={level}
      setting={setting}
      choices={choices}
      onSave={(value) => store.save(PLAIN_CHAT_KEY, value)}
      onReset={() => store.reset(PLAIN_CHAT_KEY)}
    />
  );
}

/** The environment's presets: named model and effort combinations per role, one of them the default for new chats. */
function PresetsPanel({
  environmentId,
  models,
  level,
  store,
}: {
  environmentId: number;
  models: ModelInfo[];
  level: SettingsLevel;
  store: LevelSettings;
}) {
  const [presets, setPresets] = useState<Preset[]>([]);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [modelId, setModelId] = useState("");
  const [effort, setEffort] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const nameInput = useRef<HTMLInputElement>(null);

  const reload = useCallback(async () => {
    const { data } = await api.GET("/api/v1/environments/{environment_id}/presets", {
      params: { path: { environment_id: environmentId } },
    });
    setPresets(data ?? []);
  }, [environmentId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    if (!modelId && models[0]) {
      setModelId(models[0].id);
      setEffort(models[0].default_effort);
    }
  }, [models, modelId]);

  useEffect(() => {
    if (adding) nameInput.current?.focus();
  }, [adding]);

  async function add(event: FormEvent) {
    event.preventDefault();
    const { error } = await api.POST("/api/v1/environments/{environment_id}/presets", {
      params: { path: { environment_id: environmentId } },
      body: { name: name.trim(), model: modelId, effort },
    });
    setMessage(error ? errorMessage(error) : null);
    if (!error) {
      setName("");
      setAdding(false);
    }
    await reload();
  }

  const newModel = models.find((item) => item.id === modelId);
  return (
    <Panel
      title="Model presets"
      flush
      aside={
        <button type="button" className="button button-primary" onClick={() => setAdding((open) => !open)}>
          {adding ? "Cancel" : "+ Add preset"}
        </button>
      }
    >
      <div className="wb-table-scroll settings-scroll">
        <table className="wb-table settings-presets">
          <thead>
            <tr>
              <th scope="col" title="Default for new conversations">
                <span className="visually-hidden">Default</span>★
              </th>
              <th scope="col">Name</th>
              <th scope="col">Main model · effort</th>
              <th scope="col" title={WORKER_HINT}>
                Worker
              </th>
              <th scope="col" title={UTILITY_HINT}>
                Utility
              </th>
              <th scope="col" title={REPLY_HINT}>
                Longest reply
              </th>
              <th scope="col">
                <span className="visually-hidden">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {presets.map((preset) => (
              <PresetRow key={preset.id} preset={preset} models={models} onChanged={reload} />
            ))}
            {presets.length === 0 && (
              <tr>
                <td colSpan={7}>
                  <EmptyState>No presets yet.</EmptyState>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {adding && (
        <form className="settings-preset-new" onSubmit={(event) => void add(event)}>
          <label className="visually-hidden" htmlFor="new-preset-name">
            New preset name
          </label>
          <input
            id="new-preset-name"
            ref={nameInput}
            placeholder="Name of the new preset"
            value={name}
            maxLength={40}
            onChange={(event) => setName(event.target.value)}
          />
          <select
            aria-label="Model of the new preset"
            value={modelId}
            onChange={(event) => {
              setModelId(event.target.value);
              setEffort(models.find((item) => item.id === event.target.value)?.default_effort ?? "");
            }}
          >
            <ModelOptions models={models} current={modelId} />
          </select>
          <select aria-label="Effort of the new preset" value={effort} onChange={(event) => setEffort(event.target.value)}>
            {(newModel?.efforts ?? []).map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
          <button type="submit" className="button button-primary" disabled={!name.trim()}>
            Add
          </button>
        </form>
      )}
      {message && <p className="form-error settings-panel-message">{message}</p>}
      <PlainChatPreset level={level} store={store} presets={presets} />
    </Panel>
  );
}

/** Models › Presets: named model and effort combinations per role, their routing, and the preset for plain chats. */
export default function PresetsSection({ level, store }: { level: SettingsLevel; store: LevelSettings }) {
  const environmentId = level.kind === "environment" ? level.environment.id : undefined;
  const models = useModels(environmentId);

  return (
    <>
      <SectionHeader
        level={level}
        store={store}
        title="Presets"
        info={
          <>
            <p>
              Presets are chosen when you start a conversation. Agents and projects can name a preset too. The default
              preset is selected for new conversations; conversations outside a project can start with another one
              (Preset for chats outside a project).
            </p>
            <p>
              Each preset has a model per role: the main model for the conversation, a worker for delegated work and a
              utility model for titles, commit messages and other small tasks. An empty role falls back: the worker to
              the default preset&apos;s worker, else the preset&apos;s main model; the utility role to the Utility model
              setting under Utility and workers.
            </p>
          </>
        }
      />
      <div className="wb-body wb-stack settings-models">
        {level.kind === "environment" ? (
          <PresetsPanel environmentId={level.environment.id} models={models} level={level} store={store} />
        ) : (
          <Panel title="Model presets" flush>
            <EmptyState>Presets are set per environment. Switch the level to an environment to change them.</EmptyState>
          </Panel>
        )}
      </div>
    </>
  );
}
