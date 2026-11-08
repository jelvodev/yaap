import { type FormEvent, useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";
import ModelOptions from "../ModelOptions";
import { modelState } from "../modelState";
import TaskEstimate from "./TaskEstimate";

type Task = components["schemas"]["TaskOut"];
type TaskIn = components["schemas"]["TaskIn"];
type Run = components["schemas"]["TaskRunOut"];
type Model = components["schemas"]["ModelOut"];

const STATUS_LABELS: Record<string, string> = {
  running: "Running",
  done: "Done",
  blocked: "Blocked",
  limit: "Limit reached",
  stopped: "Stopped",
  paused: "Paused",
  failed: "Failed",
};

function when(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "";
}

function lines(value: string): string[] {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function blankTask(model: Model | undefined): TaskIn {
  return {
    name: "",
    action: "prompt",
    prompt: "",
    eval_case: null,
    model: model?.id ?? "",
    effort: model?.default_effort ?? "medium",
    agent: null,
    preset: null,
    schedule_kind: "cron",
    cron: "0 7 * * 1-5",
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
    moments: [],
    max_turns: 10,
    max_cost: 2,
    allowed_commands: [],
    allowed_domains: [],
    conversation_mode: "new",
    enabled: true,
  };
}

function TaskEditor({
  projectId,
  task,
  models,
  onSaved,
  onCancel,
}: {
  projectId: number;
  task: Task | null;
  models: Model[];
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<TaskIn>(() =>
    task ? { ...task } : blankTask(models.find((model) => modelState(model).kind === "ok")),
  );
  const [moments, setMoments] = useState((task?.moments ?? []).join("\n"));
  const [commands, setCommands] = useState((task?.allowed_commands ?? []).join("\n"));
  const [domains, setDomains] = useState((task?.allowed_domains ?? []).join("\n"));
  const [error, setError] = useState<string | null>(null);
  const model = models.find((item) => item.id === draft.model);

  function set<K extends keyof TaskIn>(key: K, value: TaskIn[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    const body: TaskIn = { ...draft, moments: lines(moments), allowed_commands: lines(commands), allowed_domains: lines(domains) };
    const result = task
      ? await api.PUT("/api/v1/projects/{project_id}/tasks/{task_id}", { params: { path: { project_id: projectId, task_id: task.id } }, body })
      : await api.POST("/api/v1/projects/{project_id}/tasks", { params: { path: { project_id: projectId } }, body });
    if (result.error) {
      setError(errorMessage(result.error));
      return;
    }
    onSaved();
  }

  return (
    <form className="pp-task-editor" onSubmit={(event) => void save(event)}>
      <label>
        Name
        <input value={draft.name} maxLength={120} required onChange={(event) => set("name", event.target.value)} />
      </label>
      <label>
        What a run does
        <select value={draft.action} onChange={(event) => set("action", event.target.value === "eval" ? "eval" : "prompt")}>
          <option value="prompt">The agent works on a prompt</option>
          <option value="eval">Run the project's eval cases</option>
        </select>
      </label>
      {draft.action === "eval" ? (
        <label>
          Eval case (empty for every case)
          <input
            value={draft.eval_case ?? ""}
            maxLength={60}
            placeholder="every case"
            onChange={(event) => set("eval_case", event.target.value || null)}
          />
        </label>
      ) : (
        <label>
          What the agent does
          <textarea rows={4} value={draft.prompt} required onChange={(event) => set("prompt", event.target.value)} />
        </label>
      )}
      <div className="pp-row">
        <label>
          Model
          <select
            value={draft.model}
            onChange={(event) => {
              const chosen = models.find((item) => item.id === event.target.value);
              setDraft((current) => ({ ...current, model: event.target.value, effort: chosen?.default_effort ?? current.effort }));
            }}
          >
            <ModelOptions models={models} current={draft.model} />
          </select>
        </label>
        <label>
          Effort
          <select value={draft.effort} onChange={(event) => set("effort", event.target.value)}>
            {(model?.efforts ?? [draft.effort]).map((effort) => (
              <option key={effort}>{effort}</option>
            ))}
          </select>
        </label>
      </div>
      <label>
        When
        <select value={draft.schedule_kind} onChange={(event) => set("schedule_kind", event.target.value as TaskIn["schedule_kind"])}>
          <option value="cron">On a schedule (cron)</option>
          <option value="moments">At set moments</option>
          <option value="manual">Only on demand</option>
        </select>
      </label>
      {draft.schedule_kind === "cron" && (
        <div className="pp-row">
          <label>
            Cron <span className="faint small">minute hour day month weekday</span>
            <input className="mono" value={draft.cron} onChange={(event) => set("cron", event.target.value)} placeholder="0 7 * * 1-5" />
          </label>
          <label>
            Time zone
            <input value={draft.timezone} onChange={(event) => set("timezone", event.target.value)} />
          </label>
        </div>
      )}
      {draft.schedule_kind === "moments" && (
        <label>
          Moments <span className="faint small">one per line, such as 2026-12-01T09:00+01:00</span>
          <textarea className="mono" rows={3} value={moments} onChange={(event) => setMoments(event.target.value)} />
        </label>
      )}
      <div className="pp-row">
        <label>
          At most turns
          <input type="number" min={1} max={100} value={draft.max_turns} onChange={(event) => set("max_turns", Number(event.target.value))} />
        </label>
        <label>
          At most cost ($)
          <input type="number" min={0} step={0.5} value={draft.max_cost} onChange={(event) => set("max_cost", Number(event.target.value))} />
        </label>
        <label>
          Conversation
          <select value={draft.conversation_mode} onChange={(event) => set("conversation_mode", event.target.value as TaskIn["conversation_mode"])}>
            <option value="new">A new one per run</option>
            <option value="one">Always the same one</option>
          </select>
        </label>
      </div>
      <p className="faint small">
        Nobody approves during a run: what would ask is refused, unless it is allowed here or in the project's allowlists.
      </p>
      <div className="pp-row">
        <label>
          Commands allowed <span className="faint small">one per line</span>
          <textarea className="mono" rows={2} value={commands} onChange={(event) => setCommands(event.target.value)} placeholder="npm test" />
        </label>
        <label>
          Websites allowed <span className="faint small">one domain per line</span>
          <textarea className="mono" rows={2} value={domains} onChange={(event) => setDomains(event.target.value)} placeholder="registry.npmjs.org" />
        </label>
      </div>
      {error && <p className="form-error small">{error}</p>}
      <div className="pp-row">
        <button type="submit" className="button button-primary">
          {task ? "Save task" : "Add task"}
        </button>
        <button type="button" className="button button-ghost" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function TaskRuns({ projectId, task }: { projectId: number; task: Task }) {
  const [runs, setRuns] = useState<Run[] | null>(null);
  useEffect(() => {
    void api
      .GET("/api/v1/projects/{project_id}/tasks/{task_id}/runs", { params: { path: { project_id: projectId, task_id: task.id } } })
      .then(({ data }) => setRuns(data ?? []));
  }, [projectId, task.id, task.running, task.last_run_at]);
  if (runs === null) return <p className="faint small">Loading…</p>;
  if (runs.length === 0) return <p className="faint small">No runs yet.</p>;
  return (
    <ul className="pp-task-runs">
      {runs.map((run) => (
        <li key={run.id}>
          <span className={`pp-run-status pp-run-${run.status}`}>{STATUS_LABELS[run.status] ?? run.status}</span>
          <span className="faint small">
            {when(run.started_at)}
            {run.trigger === "missed" ? " · missed, run at start" : run.trigger === "manual" ? " · run now" : ""}
            {run.cost > 0 ? ` · $${run.cost.toFixed(2)}` : ""}
          </span>
          {run.conversation_id && (
            <Link className="link-button small" to={`/chat/${run.conversation_id}`}>
              Open
            </Link>
          )}
          {run.summary && <span className="pp-run-summary small">{run.summary}</span>}
        </li>
      ))}
    </ul>
  );
}

/** Scheduled tasks of the project: agent work that runs by itself, and its runs. */
export default function TasksTab({ projectId, environmentId }: { projectId: number; environmentId: number }) {
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [models, setModels] = useState<Model[]>([]);
  const [editing, setEditing] = useState<Task | "new" | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [notice, setNotice] = useState<{ error: boolean; text: string } | null>(null);
  const path = { params: { path: { project_id: projectId } } };

  const load = useCallback(async () => {
    const { data } = await api.GET("/api/v1/projects/{project_id}/tasks", path);
    setTasks(data ?? []);
  }, [projectId]);

  useEffect(() => {
    void load();
    // The project's tasks run under its model policy, so the list is asked for the project.
    void api
      .GET("/api/v1/environments/{environment_id}/models", {
        params: { path: { environment_id: environmentId }, query: { project_id: projectId } },
      })
      .then(({ data }) => setModels(data ?? []));
    const timer = window.setInterval(() => void load(), 30_000);
    return () => window.clearInterval(timer);
  }, [load, environmentId, projectId]);

  async function runNow(task: Task) {
    const { data, error } = await api.POST("/api/v1/projects/{project_id}/tasks/{task_id}/run", {
      params: { path: { project_id: projectId, task_id: task.id } },
    });
    setNotice(error ? { error: true, text: errorMessage(error) } : { error: false, text: `${task.name} started${data?.status === "failed" ? ", but failed: " + (data.summary ?? "") : "."}` });
    setOpen(task.id);
    await load();
  }

  async function toggle(task: Task) {
    const { error } = await api.PUT("/api/v1/projects/{project_id}/tasks/{task_id}", {
      params: { path: { project_id: projectId, task_id: task.id } },
      body: { ...task, enabled: !task.enabled },
    });
    if (error) setNotice({ error: true, text: errorMessage(error) });
    await load();
  }

  async function remove(task: Task) {
    const { error } = await api.DELETE("/api/v1/projects/{project_id}/tasks/{task_id}", {
      params: { path: { project_id: projectId, task_id: task.id } },
    });
    if (error) setNotice({ error: true, text: errorMessage(error) });
    await load();
  }

  if (tasks === null) return <p className="faint small pp-pad">Loading…</p>;
  if (editing !== null) {
    return (
      <div className="pp-pad">
        <TaskEditor
          projectId={projectId}
          task={editing === "new" ? null : editing}
          models={models}
          onCancel={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void load();
          }}
        />
      </div>
    );
  }
  return (
    <div className="pp-pad pp-tasks">
      <p className="faint small">
        Agent work that runs by itself: each run works towards the task as a goal, within its turn and cost limits, and
        nobody approves during it. A run missed while YAAP was off runs once when it starts.
      </p>
      <div className="pp-row">
        <button type="button" className="button button-primary" onClick={() => setEditing("new")}>
          New task
        </button>
      </div>
      {notice && <p className={notice.error ? "form-error small" : "muted small"}>{notice.text}</p>}
      {tasks.length === 0 && <p className="faint small">No tasks yet.</p>}
      <ul className="pp-task-list">
        {tasks.map((task) => (
          <li key={task.id} className={task.enabled ? "pp-task" : "pp-task pp-task-paused"}>
            <div className="pp-task-head">
              <button type="button" className="link-button" aria-expanded={open === task.id} onClick={() => setOpen(open === task.id ? null : task.id)}>
                {task.name}
              </button>
              {task.running && <span className="running-dot" aria-label="Running" />}
              <span className="faint small">
                {task.enabled ? task.schedule_text : "Paused"}
                {task.enabled && task.next_runs[0] ? ` · next ${when(task.next_runs[0])}` : ""}
              </span>
            </div>
            <div className="pp-row">
              <button type="button" className="button button-ghost" disabled={task.running} onClick={() => void runNow(task)}>
                Run now
              </button>
              <button type="button" className="button button-ghost" onClick={() => void toggle(task)}>
                {task.enabled ? "Pause" : "Resume"}
              </button>
              <button type="button" className="link-button small" onClick={() => setEditing(task)}>
                Edit
              </button>
              <button type="button" className="link-button small" disabled={task.running} onClick={() => void remove(task)}>
                Delete
              </button>
            </div>
            {open === task.id && (
              <>
                {task.next_runs.length > 1 && (
                  <p className="faint small">Next: {task.next_runs.map((moment) => when(moment)).join(" · ")}</p>
                )}
                {task.action === "prompt" && (
                  <TaskEstimate projectId={projectId} taskId={task.id} lastRunAt={task.last_run_at} />
                )}
                <TaskRuns projectId={projectId} task={task} />
              </>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
