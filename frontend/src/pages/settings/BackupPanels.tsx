import { type FormEvent, useCallback, useEffect, useState } from "react";

import { api, errorMessage } from "../../api/client";
import { formatSize } from "../../api/attachments";
import type { components } from "../../api/schema";
import { Badge, EmptyState, FieldRow, Panel } from "../../components/workbench/Workbench";

type Run = components["schemas"]["BackupRunOut"];
type DestinationState = components["schemas"]["DestinationStateOut"];

interface Schedule {
  enabled: boolean;
  cron: string;
  keep: number;
}

/** A destination as stored in the setting backup_destinations. */
interface DestinationConfig {
  name: string;
  kind: "folder" | "smb";
  enabled: boolean;
  keep: number;
  path?: string;
  server?: string;
  share?: string;
  folder?: string;
  user?: string;
  domain?: string;
  password_entry?: string;
  require_encryption?: boolean;
}

async function readSetting<T>(key: string, fallback: T): Promise<T> {
  const { data } = await api.GET("/api/v1/settings");
  const found = data?.find((item) => item.key === key);
  return (found?.value as T | undefined) ?? fallback;
}

async function saveSetting(key: string, value: unknown): Promise<string | null> {
  const { error } = await api.PUT("/api/v1/settings/{key}", { params: { path: { key } }, body: { value } });
  return error ? errorMessage(error, "Not saved.") : null;
}

function when(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString() : "—";
}

/** The schedule: on or off, a cron expression in the global time zone, how many are kept. */
export function SchedulePanel({ onChanged }: { onChanged: () => void }) {
  const [schedule, setSchedule] = useState<Schedule | null>(null);
  const [draft, setDraft] = useState<Schedule | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    const value = await readSetting<Schedule>("backup_schedule", { enabled: true, cron: "0 3 * * *", keep: 7 });
    setSchedule(value);
    setDraft(value);
    const { data } = await api.GET("/api/v1/backups/status");
    setNext(data?.next_at ?? null);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!draft) return;
    const problem = await saveSetting("backup_schedule", draft);
    setMessage(problem ?? "Schedule saved.");
    if (!problem) {
      await load();
      onChanged();
    }
  }

  if (!schedule || !draft) return null;
  const changed = JSON.stringify(schedule) !== JSON.stringify(draft);
  return (
    <Panel title="Schedule" flush aside={draft.enabled && next ? `Next: ${when(next)}` : "Off"}>
      <form onSubmit={(event) => void save(event)}>
        <FieldRow label="On a schedule" hint="Also when YAAP was down at that time: a missed backup runs once at start">
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })}
            />
            Make backups by themselves
          </label>
        </FieldRow>
        <FieldRow label="When" hint="Cron, in the global time zone; 0 3 * * * is every day at 03:00">
          <input
            aria-label="Cron expression"
            className="mono"
            value={draft.cron}
            maxLength={100}
            onChange={(event) => setDraft({ ...draft, cron: event.target.value })}
          />
        </FieldRow>
        <FieldRow label="Keep" hint="In data/backups; each destination has its own number">
          <input
            aria-label="Backups to keep"
            type="number"
            min={1}
            max={365}
            value={draft.keep}
            onChange={(event) => setDraft({ ...draft, keep: Number(event.target.value) })}
          />
        </FieldRow>
        <div className="settings-inline-form backup-save">
          <button type="submit" className="button" disabled={!changed}>
            Save schedule
          </button>
          {message && <span className="small">{message}</span>}
        </div>
      </form>
    </Panel>
  );
}

const EMPTY_FOLDER: DestinationConfig = { name: "", kind: "folder", enabled: true, keep: 7, path: "/backup" };

/** Destinations: folders mounted under /backup and network shares. */
export function DestinationsPanel({ refresh, onFetched }: { refresh: number; onFetched: (name: string) => void }) {
  const [configs, setConfigs] = useState<DestinationConfig[]>([]);
  const [states, setStates] = useState<DestinationState[] | null>(null);
  const [passwords, setPasswords] = useState<string[]>([]);
  const [draft, setDraft] = useState<DestinationConfig>(EMPTY_FOLDER);
  const [open, setOpen] = useState<string | null>(null);
  const [message, setMessage] = useState<{ error: boolean; text: string } | null>(null);

  const load = useCallback(async () => {
    setConfigs(await readSetting<DestinationConfig[]>("backup_destinations", []));
    const { data } = await api.GET("/api/v1/backups/destinations");
    setStates(data ?? []);
    const { data: entries } = await api.GET("/api/v1/vault/entries", { params: { query: {} } });
    setPasswords((entries ?? []).filter((entry) => entry.kind === "smb_password").map((entry) => entry.name));
  }, []);

  useEffect(() => {
    void load();
  }, [load, refresh]);

  async function store(next: DestinationConfig[], done: string) {
    const problem = await saveSetting("backup_destinations", next);
    setMessage(problem ? { error: true, text: problem } : { error: false, text: done });
    if (!problem) await load();
    return problem;
  }

  async function add(event: FormEvent) {
    event.preventDefault();
    const clean: DestinationConfig =
      draft.kind === "folder"
        ? { name: draft.name.trim(), kind: "folder", enabled: true, keep: draft.keep, path: draft.path?.trim() ?? "" }
        : {
            name: draft.name.trim(),
            kind: "smb",
            enabled: true,
            keep: draft.keep,
            server: draft.server?.trim() ?? "",
            share: draft.share?.trim() ?? "",
            folder: draft.folder?.trim() ?? "",
            user: draft.user?.trim() ?? "",
            domain: draft.domain?.trim() ?? "",
            password_entry: draft.password_entry ?? "",
            require_encryption: draft.require_encryption ?? true,
          };
    const problem = await store([...configs, clean], `${clean.name} added.`);
    if (!problem) setDraft(EMPTY_FOLDER);
  }

  async function test(name: string) {
    const { data, error } = await api.POST("/api/v1/backups/destinations/{name}/test", { params: { path: { name } } });
    setMessage(error || !data ? { error: true, text: errorMessage(error, "The test failed.") } : { error: !data.ok, text: `${name}: ${data.message}` });
    await load();
  }

  async function fetchBack(destination: string, backup: string) {
    const { error } = await api.POST("/api/v1/backups/destinations/{name}/fetch/{backup}", {
      params: { path: { name: destination, backup } },
    });
    if (error) {
      setMessage({ error: true, text: errorMessage(error, "The backup could not be copied back.") });
      return;
    }
    setMessage({ error: false, text: `${backup} copied into YAAP's own backups.` });
    onFetched(backup);
  }

  function patch(name: string, change: Partial<DestinationConfig>, done: string) {
    void store(configs.map((config) => (config.name === name ? { ...config, ...change } : config)), done);
  }

  return (
    <Panel title="Destinations" flush aside="Each backup is copied there and checked (size and SHA-256)">
      {states === null ? (
        <EmptyState>Loading…</EmptyState>
      ) : states.length === 0 ? (
        <EmptyState>
          Backups stay in data/backups only, on the same disk as the data. Add a folder (mounted under /backup) or a
          network share so a backup survives that disk.
        </EmptyState>
      ) : (
        <div className="wb-table-scroll">
          <table className="wb-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Where</th>
                <th>State</th>
                <th className="wb-num">Free</th>
                <th className="wb-num">Keep</th>
                <th>
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {states.map((state) => (
                <tr key={state.name}>
                  <td>
                    <button type="button" className="link-button" onClick={() => setOpen(open === state.name ? null : state.name)}>
                      {state.name}
                    </button>{" "}
                    {!state.enabled && <Badge tone="off">off</Badge>}
                  </td>
                  <td className="mono small">{state.where}</td>
                  <td>
                    <Badge tone={state.ok ? "ok" : "danger"}>{state.ok ? "reachable" : "problem"}</Badge>{" "}
                    <span className="small">{state.message}</span>
                  </td>
                  <td className="wb-num">{state.free_space !== null ? formatSize(state.free_space) : "—"}</td>
                  <td className="wb-num">{state.keep}</td>
                  <td className="wb-num backup-actions">
                    <button type="button" className="button button-ghost" onClick={() => void test(state.name)}>
                      Test
                    </button>
                    <button
                      type="button"
                      className="button button-ghost"
                      onClick={() => patch(state.name, { enabled: !state.enabled }, `${state.name} switched ${state.enabled ? "off" : "on"}.`)}
                    >
                      {state.enabled ? "Switch off" : "Switch on"}
                    </button>
                    <button
                      type="button"
                      className="button button-ghost danger-link"
                      onClick={() => void store(configs.filter((config) => config.name !== state.name), `${state.name} removed; its backups stay where they are.`)}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {open && (
        <div className="backup-remote">
          <h4>Backups at {open}</h4>
          {(states?.find((state) => state.name === open)?.backups ?? []).length === 0 ? (
            <p className="faint small">None there.</p>
          ) : (
            <ul>
              {states
                ?.find((state) => state.name === open)
                ?.backups.map((backup) => (
                  <li key={backup.name}>
                    <span className="mono small">{backup.name}</span> <span className="faint small">{formatSize(backup.size)}</span>{" "}
                    <button type="button" className="link-button" onClick={() => void fetchBack(open, backup.name)}>
                      Copy back to restore
                    </button>
                  </li>
                ))}
            </ul>
          )}
        </div>
      )}

      <form className="backup-add" onSubmit={(event) => void add(event)}>
        <h4>Add a destination</h4>
        <div className="vault-add-row">
          <select
            aria-label="Kind of destination"
            value={draft.kind}
            onChange={(event) =>
              setDraft(
                event.target.value === "folder"
                  ? { ...EMPTY_FOLDER, name: draft.name }
                  : { name: draft.name, kind: "smb", enabled: true, keep: 7, require_encryption: true },
              )
            }
          >
            <option value="folder">Folder</option>
            <option value="smb">Network share (SMB)</option>
          </select>
          <input
            aria-label="Destination name"
            placeholder="Name, e.g. External disk"
            value={draft.name}
            maxLength={40}
            onChange={(event) => setDraft({ ...draft, name: event.target.value })}
          />
          <label className="vault-date">
            <span className="faint small">Keep</span>
            <input
              type="number"
              min={1}
              max={365}
              value={draft.keep}
              onChange={(event) => setDraft({ ...draft, keep: Number(event.target.value) })}
            />
          </label>
        </div>
        {draft.kind === "folder" ? (
          <>
            <p className="faint small">
              A folder inside the container under /backup. Mount a folder of your computer there in
              docker-compose.override.yml (see docker-compose.override.example.yml).
            </p>
            <input
              aria-label="Folder"
              className="mono"
              value={draft.path ?? ""}
              onChange={(event) => setDraft({ ...draft, path: event.target.value })}
            />
          </>
        ) : (
          <>
            <div className="vault-add-row">
              <input aria-label="Server" placeholder="Server, e.g. nas or 192.168.1.20" value={draft.server ?? ""} onChange={(event) => setDraft({ ...draft, server: event.target.value })} />
              <input aria-label="Share" placeholder="Share, e.g. backups" value={draft.share ?? ""} onChange={(event) => setDraft({ ...draft, share: event.target.value })} />
              <input aria-label="Folder on the share" placeholder="Folder (optional), e.g. yaap" value={draft.folder ?? ""} onChange={(event) => setDraft({ ...draft, folder: event.target.value })} />
            </div>
            <div className="vault-add-row">
              <input aria-label="User" placeholder="User" value={draft.user ?? ""} onChange={(event) => setDraft({ ...draft, user: event.target.value })} />
              <input aria-label="Domain" placeholder="Domain (optional)" value={draft.domain ?? ""} onChange={(event) => setDraft({ ...draft, domain: event.target.value })} />
              <select
                aria-label="Password from the vault"
                value={draft.password_entry ?? ""}
                onChange={(event) => setDraft({ ...draft, password_entry: event.target.value })}
              >
                <option value="">No password</option>
                {passwords.map((name) => (
                  <option key={name} value={name}>
                    Vault: {name}
                  </option>
                ))}
              </select>
            </div>
            <label className="checkbox-row small">
              <input
                type="checkbox"
                checked={draft.require_encryption ?? true}
                onChange={(event) => setDraft({ ...draft, require_encryption: event.target.checked })}
              />
              Require encryption (SMB 3). Switch off only for a server that cannot encrypt, on a network you trust.
            </label>
            <p className="faint small">
              The password is a vault entry of kind Network share password at Global (Settings › Connections › Vault).
            </p>
          </>
        )}
        <div>
          <button type="submit" className="button" disabled={!draft.name.trim()}>
            Add destination
          </button>
        </div>
      </form>
      {message && <p className={message.error ? "form-error small backup-message" : "form-ok small backup-message"}>{message.text}</p>}
    </Panel>
  );
}

const STATUS_TONE: Record<string, "ok" | "warn" | "danger" | "neutral"> = {
  ok: "ok",
  partial: "warn",
  failed: "danger",
  running: "neutral",
};

/** The recent backups with how they went per destination. */
export function RunsPanel({ refresh }: { refresh: number }) {
  const [runs, setRuns] = useState<Run[] | null>(null);
  useEffect(() => {
    void api.GET("/api/v1/backups/runs").then(({ data }) => setRuns(data ?? []));
  }, [refresh]);
  return (
    <Panel title="Recent runs" flush>
      {runs === null ? (
        <EmptyState>Loading…</EmptyState>
      ) : runs.length === 0 ? (
        <EmptyState>No backups made since this was added.</EmptyState>
      ) : (
        <div className="wb-table-scroll settings-scroll">
          <table className="wb-table">
            <thead>
              <tr>
                <th>Started</th>
                <th>How</th>
                <th>Result</th>
                <th>Destinations</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id}>
                  <td className="small">{when(run.started_at)}</td>
                  <td>{run.trigger === "manual" ? "by hand" : run.trigger === "missed" ? "missed, at start" : "scheduled"}</td>
                  <td>
                    <Badge tone={STATUS_TONE[run.status] ?? "neutral"}>{run.status}</Badge>{" "}
                    {run.size !== null && <span className="small">{formatSize(run.size)}</span>}
                    {run.error && <div className="small form-error">{run.error}</div>}
                  </td>
                  <td className="small">
                    {run.results.length === 0
                      ? "—"
                      : run.results.map((result) => (
                          <div key={String(result.destination)}>
                            {result.ok ? "✓" : "✗"} {String(result.destination)}: {String(result.message)}
                          </div>
                        ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
