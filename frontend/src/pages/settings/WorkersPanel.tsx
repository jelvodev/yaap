import { useEffect, useState } from "react";

import { type ModelInfo, api } from "../../api/client";
import type { components } from "../../api/schema";
import ModelOptions from "../../components/ModelOptions";
import { modelState, stateMark } from "../../components/modelState";
import { Badge, EmptyState, Panel } from "../../components/workbench/Workbench";
import { type LevelSettings, type SettingsLevel, isOwn } from "./shared";
import { loadClaudeCodeStatus, loadCodexStatus } from "./StatusFields";

type Worker = components["schemas"]["WorkerOut"];
type CliValue = { model: string; effort: string };

/** The coding agent CLIs delegate can name as worker, with the setting that holds their model. */
const CLI_WORKERS = [
  { name: "claude_code", label: "Claude Code", setting: "claude_code_worker", provider: "anthropic", load: loadClaudeCodeStatus },
  { name: "codex", label: "Codex", setting: "codex_worker", provider: "openai", load: loadCodexStatus },
] as const;

type CliWorker = (typeof CLI_WORKERS)[number];

/** Whether the CLI is installed and signed in; the sign-in itself is under Connections. */
function SignInBadge({ cli }: { cli: CliWorker }) {
  const [status, setStatus] = useState<{ installed: boolean; logged_in: boolean } | null | undefined>(undefined);
  useEffect(() => {
    let current = true;
    void cli.load().then((data) => current && setStatus(data));
    return () => {
      current = false;
    };
  }, [cli]);
  if (status === undefined) return <span className="faint small">checking…</span>;
  if (status === null) return <span className="faint small">status unknown</span>;
  return (
    <Badge tone={!status.installed ? "danger" : status.logged_in ? "ok" : "warn"}>
      {!status.installed ? "not installed" : status.logged_in ? "signed in" : "not signed in"}
    </Badge>
  );
}

function BillingBadge({ billing }: { billing: Worker["billing"] }) {
  return billing === "subscription" ? <Badge tone="ok">subscription</Badge> : <Badge tone="off">per token</Badge>;
}

/** Whether a preset's worker can run, and if not why: the model policy, or a missing key or connection. */
function WorkerStatus({ worker }: { worker: Worker }) {
  const state = modelState(worker);
  if (state.kind === "ok") return <Badge tone="ok">ready</Badge>;
  if (state.kind === "not_allowed") {
    return (
      <Badge tone="danger" title={state.reason ?? undefined}>
        {stateMark(state)}: {state.reason ?? "the model policy forbids it"}
      </Badge>
    );
  }
  return (
    <Badge tone="warn" title={state.reason ?? undefined}>
      {state.reason ?? "unavailable"}
    </Badge>
  );
}

/** A CLI worker: its model and effort are a setting of this level; its sign-in is shared by every environment. */
function CliWorkerRow({
  cli,
  level,
  store,
  models,
  info,
}: {
  cli: CliWorker;
  level: SettingsLevel;
  store: LevelSettings;
  models: ModelInfo[];
  info: Worker | undefined;
}) {
  const [message, setMessage] = useState<string | null>(null);
  const setting = store.find(cli.setting);
  const value = (setting?.value ?? null) as CliValue | null;
  // The CLI signs in itself, so its models need no key here; whether the policy allows the CLI's connection is the
  // status beside it, not the API connection's answer the model list carries.
  const choices = models
    .filter((model) => model.provider === cli.provider)
    .map((model) => ({ ...model, available: true, allowed: true, unavailable_reason: null }));
  const model = choices.find((item) => item.id === value?.model);
  const save = (next: CliValue) => void store.save(cli.setting, next).then(setMessage);

  return (
    <>
      <tr>
        <td>
          <strong>{cli.label}</strong>
          <div className="faint small mono">{cli.name}</div>
        </td>
        <td>
          <div className="settings-role-cell">
            <label className="visually-hidden" htmlFor={`worker-model-${cli.name}`}>
              {cli.label} model
            </label>
            <select
              id={`worker-model-${cli.name}`}
              value={value?.model ?? ""}
              disabled={!setting || choices.length === 0}
              onChange={(event) => {
                const next = choices.find((item) => item.id === event.target.value);
                if (next) save({ model: next.id, effort: value && next.efforts.includes(value.effort) ? value.effort : next.default_effort });
              }}
            >
              {!model && <option value={value?.model ?? ""}>{value?.model ?? "–"}</option>}
              <ModelOptions models={choices} current={value?.model} />
            </select>
            {model && (
              <select
                aria-label={`${cli.label} effort`}
                value={value?.effort ?? model.default_effort}
                onChange={(event) => save({ model: model.id, effort: event.target.value })}
              >
                {model.efforts.map((effort) => (
                  <option key={effort} value={effort}>
                    {effort}
                  </option>
                ))}
              </select>
            )}
            {isOwn(setting) && (
              <button
                type="button"
                className="button button-ghost"
                title={level.kind === "global" ? "Back to YAAP's default" : "Back to the Global choice"}
                onClick={() => void store.reset(cli.setting).then(setMessage)}
              >
                Reset
              </button>
            )}
          </div>
        </td>
        <td>
          {cli.label} <BillingBadge billing="subscription" />
        </td>
        <td>
          <SignInBadge cli={cli} />
          {info && modelState(info).kind === "not_allowed" && <Badge tone="danger">not allowed</Badge>}
          {info && !info.available && (
            <div className="faint small" title={info.unavailable_reason ?? undefined}>
              {info.unavailable_reason}
            </div>
          )}
        </td>
      </tr>
      {message && (
        <tr>
          <td colSpan={4} className="form-error">
            {message}
          </td>
        </tr>
      )}
    </>
  );
}

/**
 * Who delegate can hand a task to: each preset's worker, by the preset's name, and the coding agent
 * CLIs. Presets are edited in the Presets tab; a CLI's model and effort here.
 */
export default function WorkersPanel({
  level,
  store,
  models,
}: {
  level: SettingsLevel;
  store: LevelSettings;
  models: ModelInfo[];
}) {
  const environmentId = level.kind === "environment" ? level.environment.id : null;
  const [workers, setWorkers] = useState<Worker[]>([]);

  useEffect(() => {
    if (environmentId === null) {
      setWorkers([]);
      return;
    }
    let current = true;
    void api
      .GET("/api/v1/environments/{environment_id}/workers", { params: { path: { environment_id: environmentId } } })
      .then(({ data }) => current && setWorkers(data ?? []));
    return () => {
      current = false;
    };
  }, [environmentId, store.settings]);

  const presets = workers.filter((worker) => worker.kind === "preset");
  return (
    <Panel title="Workers" aside={<span>who delegated work can go to, by the name the agent uses</span>} flush>
      <div className="wb-table-scroll settings-scroll">
        <table className="wb-table settings-presets">
          <thead>
            <tr>
              <th scope="col">Worker</th>
              <th scope="col">Model · effort</th>
              <th scope="col">Runs through</th>
              <th scope="col">Status</th>
            </tr>
          </thead>
          <tbody>
            {presets.map((worker) => (
              <tr key={worker.name}>
                <td>
                  {worker.label} {worker.is_default && <Badge tone="level">default</Badge>}
                </td>
                <td>
                  {worker.model_name} · {worker.effort}
                </td>
                <td>
                  {worker.connection || "–"} <BillingBadge billing={worker.billing} />
                </td>
                <td>
                  <WorkerStatus worker={worker} />
                </td>
              </tr>
            ))}
            {level.kind === "global" && (
              <tr>
                <td colSpan={4}>
                  <EmptyState>Presets are set per environment; their workers show at an environment's level.</EmptyState>
                </td>
              </tr>
            )}
            {CLI_WORKERS.map((cli) => (
              <CliWorkerRow
                key={cli.name}
                cli={cli}
                level={level}
                store={store}
                models={models}
                info={workers.find((worker) => worker.name === cli.name)}
              />
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
