import { type FormEvent, useState } from "react";

import {
  DEFAULT_CARDS_PER_DAY,
  DEFAULT_LABEL,
  KIND_LABELS,
  MAX_CARDS_PER_DAY,
  TRIGGER_KINDS,
  type TriggerKind,
  cardsText,
  isTriggerKind,
  triggerName,
  triggerWhat,
} from "./triggerLogic";
import { type Task, type TaskIn, triggerTask, useBoardTriggers } from "./useBoardTriggers";

function when(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "never";
}

function TriggerForm({
  onAdd,
  onCancel,
  busy,
}: {
  onAdd: (body: TaskIn) => Promise<boolean>;
  onCancel: () => void;
  busy: boolean;
}) {
  const [kind, setKind] = useState<TriggerKind>("ci_failed");
  const [label, setLabel] = useState(DEFAULT_LABEL);
  const [command, setCommand] = useState("");
  const [cron, setCron] = useState("0 * * * *");
  const [perDay, setPerDay] = useState(DEFAULT_CARDS_PER_DAY);

  async function save(event: FormEvent) {
    event.preventDefault();
    const config = {
      kind,
      label: kind === "new_issue" ? label.trim() || DEFAULT_LABEL : null,
      command: kind === "command_failed" ? command.trim() : null,
      cards_per_day: perDay,
    };
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    if (await onAdd(triggerTask(triggerName(kind, label, command), config, cron.trim(), timezone))) onCancel();
  }

  return (
    <form className="pp-task-editor" onSubmit={(event) => void save(event)}>
      <label>
        Make a card when
        <select value={kind} onChange={(event) => isTriggerKind(event.target.value) && setKind(event.target.value)}>
          {TRIGGER_KINDS.map((item) => (
            <option key={item} value={item}>
              {KIND_LABELS[item]}
            </option>
          ))}
        </select>
      </label>
      {kind === "new_issue" && (
        <label>
          Label
          <input value={label} maxLength={50} onChange={(event) => setLabel(event.target.value)} />
        </label>
      )}
      {kind === "command_failed" && (
        <label>
          Command (runs in the runner without you, such as npm test)
          <input value={command} maxLength={200} required onChange={(event) => setCommand(event.target.value)} />
        </label>
      )}
      <label>
        Schedule (cron)
        <input value={cron} required onChange={(event) => setCron(event.target.value)} />
      </label>
      <label>
        At most cards a day
        <input
          type="number"
          min={1}
          max={MAX_CARDS_PER_DAY}
          value={perDay}
          onChange={(event) => setPerDay(Math.min(MAX_CARDS_PER_DAY, Math.max(1, Number(event.target.value) || 1)))}
        />
      </label>
      <div className="pp-row">
        <button type="submit" className="button button-primary" disabled={busy}>
          Add trigger
        </button>
        <button type="button" className="button button-ghost" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function TriggerRow({
  task,
  busy,
  actions,
}: {
  task: Task;
  busy: boolean;
  actions: ReturnType<typeof useBoardTriggers>;
}) {
  const config = task.config;
  const what = config ? triggerWhat(config.kind, config.label, config.command) : task.name;
  return (
    <li className={task.enabled ? "pp-task" : "pp-task pp-task-paused"}>
      <div className="pp-task-head">
        <label>
          <input type="checkbox" checked={task.enabled} disabled={busy} onChange={() => void actions.toggle(task)} />{" "}
          {what}
        </label>
        {task.running && <span className="running-dot" aria-label="Running" />}
      </div>
      <p className="faint small">
        {task.schedule_text} · last run {when(task.last_run_at)} · {cardsText(task.cards_this_week)}
        {config ? ` · at most ${config.cards_per_day} a day` : ""}
      </p>
      <div className="pp-row">
        <button
          type="button"
          className="button button-ghost"
          disabled={busy || task.running}
          onClick={() => void actions.runNow(task)}
        >
          Run now
        </button>
        <button
          type="button"
          className="link-button small"
          disabled={busy || task.running}
          onClick={() => void actions.remove(task)}
        >
          Delete
        </button>
      </div>
    </li>
  );
}

/** The project's board triggers: things YAAP polls on a schedule that put cards in Inbox, without a model. */
export default function BoardTriggers({ projectId }: { projectId: number }) {
  const actions = useBoardTriggers(projectId);
  const [adding, setAdding] = useState(false);
  const { triggers, error, busy } = actions;
  return (
    <div className="pp-tasks">
      <h4>Triggers</h4>
      <p className="faint small">
        Cards in Inbox when CI fails, an issue gets a label, a dependency update comes in or a scheduled command fails.
        Triggers make no model call; each makes at most its number of cards a day, and none while the board is off.
      </p>
      {error && <p className="form-error small">{error}</p>}
      {triggers === null ? (
        <p className="faint small">Loading…</p>
      ) : (
        <ul className="pp-task-list">
          {triggers.map((task) => (
            <TriggerRow key={task.id} task={task} busy={busy} actions={actions} />
          ))}
          {triggers.length === 0 && <li className="faint small">No triggers yet.</li>}
        </ul>
      )}
      {adding ? (
        <TriggerForm onAdd={actions.add} onCancel={() => setAdding(false)} busy={busy} />
      ) : (
        <div className="pp-row">
          <button type="button" className="button button-ghost" onClick={() => setAdding(true)}>
            Add a trigger
          </button>
        </div>
      )}
    </div>
  );
}
