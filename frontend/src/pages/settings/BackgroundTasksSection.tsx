import { useState } from "react";
import { Link } from "react-router-dom";

import { Badge, EmptyState, FieldRow, Panel } from "../../components/workbench/Workbench";
import { useEnvironment } from "../../environment/EnvironmentProvider";
import { type BackgroundTask, type ScheduledWork, useBackgroundTasks } from "./useBackgroundTasks";
import {
  type LevelSettings,
  type SettingsLevel,
  SectionHeader,
  TAB_OVERRIDE_KEYS,
  formatDate,
  isOwn,
  levelTitle,
} from "./shared";

const OFF_KEY = "utility_tasks_off";
const ALWAYS_ON = "Always on: it protects you from outside content.";

function namesOf(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function formatCost(value: number): string {
  return `$${value.toFixed(value >= 1 ? 2 : 4)}`;
}

function TaskLinks({ task }: { task: BackgroundTask }) {
  return (
    <span className="settings-inherited">
      {task.builtin_item && (
        <>
          <Link to={`/blueprint?builtin=${encodeURIComponent(task.builtin_item)}`}>Instructions</Link>
          {" · "}
        </>
      )}
      <Link to="/settings/models/utility">Model setting</Link>
    </span>
  );
}

function LastRuns({ task }: { task: BackgroundTask }) {
  if (task.last_runs.length === 0) return null;
  return (
    <details>
      <summary>Last runs</summary>
      <ul>
        {task.last_runs.map((run) => (
          <li key={`${run.created_at}-${run.conversation_id ?? ""}`}>
            {formatDate(run.created_at)} · {run.model}
            {run.cost !== null && ` · ${formatCost(run.cost)}`}
            {run.summary && ` · ${run.summary}`}
            {run.conversation_id !== null && (
              <>
                {" · "}
                <Link to={`/chat/${run.conversation_id}`}>#{run.conversation_id}</Link>
              </>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
}

function TaskRow({
  task,
  enabled,
  numbers,
  busy,
  onSwitch,
}: {
  task: BackgroundTask;
  enabled: boolean;
  numbers: boolean;
  busy: boolean;
  onSwitch: (on: boolean) => void;
}) {
  return (
    <FieldRow
      label={task.label}
      hint={numbers ? `${task.model ?? "Utility model"} · ${task.runs} runs · ${formatCost(task.cost)}` : undefined}
    >
      {task.switchable ? (
        <label>
          <input
            type="checkbox"
            checked={enabled}
            disabled={busy}
            onChange={(event) => onSwitch(event.target.checked)}
          />{" "}
          {enabled ? "On" : "Off"}
        </label>
      ) : (
        <Badge tone="level">{ALWAYS_ON}</Badge>
      )}
      {numbers && <TaskLinks task={task} />}
      {numbers && <LastRuns task={task} />}
    </FieldRow>
  );
}

function ScheduledRow({ work }: { work: ScheduledWork }) {
  return (
    <FieldRow label={work.name} hint={work.schedule}>
      <Badge tone={work.enabled ? "accent" : "neutral"}>{work.enabled ? "On" : "Off"}</Badge>
      <p className="settings-help">{work.description}</p>
      {work.setting_key && <span className="settings-inherited">Setting: {work.setting_key}</span>}
    </FieldRow>
  );
}

/**
 * Models › Background tasks: what YAAP starts by itself, with a switch for the tasks that can be off, and the
 * scheduled work. At Global only the switches show, for the tasks of the active environment, without numbers.
 */
export default function BackgroundTasksSection({ level, store }: { level: SettingsLevel; store: LevelSettings }) {
  const { active } = useEnvironment();
  const environment = level.kind === "environment";
  const environmentId = level.kind === "environment" ? level.environment.id : (active?.id ?? null);
  const { tasks, loading, error, reload } = useBackgroundTasks(environmentId);
  const setting = store.find(OFF_KEY);
  const off = namesOf(setting?.value);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function setSwitch(task: string, on: boolean) {
    setBusy(true);
    const next = on ? off.filter((name) => name !== task) : [...off, task];
    setMessage(await store.save(OFF_KEY, next));
    await reload();
    setBusy(false);
  }

  async function resetSwitches() {
    setBusy(true);
    setMessage(await store.reset(OFF_KEY));
    await reload();
    setBusy(false);
  }

  const byYaap = tasks?.tasks.filter((task) => task.started_by === "yaap") ?? [];
  const byUser = tasks?.tasks.filter((task) => task.started_by === "user") ?? [];
  const shown = environment ? byYaap : byYaap.filter((task) => task.switchable);

  return (
    <>
      <SectionHeader
        level={level}
        store={store}
        title="Background tasks"
        overrideKeys={TAB_OVERRIDE_KEYS.background}
        onReset={() => void reload()}
        info={
          <>
            <p>
              Background tasks are small jobs YAAP starts by itself, such as titles, memory suggestions and routing.
              Switch one off and it stops running; the rest of YAAP keeps working. The list of switched-off tasks
              replaces the one of the level above as a whole.
            </p>
            <p>
              Each task runs on the utility model unless it has a model of its own, and has built-in instructions you
              can read in the Blueprint.
            </p>
          </>
        }
      />
      <div className="wb-body wb-body-narrow wb-stack">
        {message && <p className="form-error">{message}</p>}
        {error && <p className="form-error">{error}</p>}
        {loading && <p className="settings-help">Loading background tasks...</p>}
        {!loading && !error && !tasks && <EmptyState>Create an environment to see its background tasks.</EmptyState>}
        {tasks && (
          <>
            <Panel
              title={
                environment ? "Started by YAAP" : `Switches (tasks of ${active?.name ?? "the active environment"})`
              }
              aside={
                setting && isOwn(setting) ? (
                  <button
                    type="button"
                    className="button button-ghost"
                    disabled={busy}
                    onClick={() => void resetSwitches()}
                  >
                    {environment ? "Reset to inherited" : "Reset to default"}
                  </button>
                ) : undefined
              }
              flush
            >
              {shown.map((task) => (
                <TaskRow
                  key={task.task}
                  task={task}
                  enabled={!off.includes(task.task)}
                  numbers={environment}
                  busy={busy || !setting}
                  onSwitch={(on) => void setSwitch(task.task, on)}
                />
              ))}
              {!environment && (
                <p className="settings-help">Set for {levelTitle(level)}; an environment can override it.</p>
              )}
            </Panel>
            {environment && byUser.length > 0 && (
              <Panel title="Started by you" flush>
                {byUser.map((task) => (
                  <FieldRow key={task.task} label={task.label} hint="Started by you; no switch">
                    <span>
                      {task.model ?? "Utility model"} · {task.runs} runs · {formatCost(task.cost)}
                    </span>
                    <TaskLinks task={task} />
                  </FieldRow>
                ))}
              </Panel>
            )}
            {environment && (
              <Panel title="Scheduled work" flush>
                {tasks.scheduled.map((work) => (
                  <ScheduledRow key={work.name} work={work} />
                ))}
              </Panel>
            )}
          </>
        )}
      </div>
    </>
  );
}
