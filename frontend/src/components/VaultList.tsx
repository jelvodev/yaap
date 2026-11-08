import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { components } from "../api/schema";
import { Badge, EmptyState } from "./workbench/Workbench";
import { useConfirm } from "./dialog/ConfirmDialog";

type Entry = components["schemas"]["VaultEntryOut"];
type Kind = components["schemas"]["VaultKindOut"];
type Detail = components["schemas"]["VaultDetailOut"];

type Filter = "all" | "agent" | "expiring" | "unused";

const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "agent", label: "Agent may use" },
  { value: "expiring", label: "Expires soon" },
  { value: "unused", label: "Never used" },
];

function day(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleDateString() : "—";
}

function moment(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString() : "never";
}

/** The local date of a moment, as a date input wants it (yyyy-mm-dd). */
function dateInput(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  const pad = (number: number) => String(number).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function ExpiryBadge({ entry }: { entry: Entry }) {
  if (entry.expiry === "expired") return <Badge tone="danger">expired {day(entry.expires_at)}</Badge>;
  if (entry.expiry === "soon") return <Badge tone="warn">expires {day(entry.expires_at)}</Badge>;
  return entry.expires_at ? <span className="faint small">until {day(entry.expires_at)}</span> : null;
}

/**
 * The vault at one level: YAAP's own keys and the secrets the agent uses by name, with what the level
 * inherits. Values are write-only; the list shows the last four characters at most.
 */
export default function VaultList({ environmentId, projectId }: { environmentId?: number; projectId?: number }) {
  const [entries, setEntries] = useState<Entry[] | null>(null);
  const [kinds, setKinds] = useState<Kind[]>([]);
  const [kindFilter, setKindFilter] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [openId, setOpenId] = useState<number | null>(null);
  const level = projectId !== undefined ? "project" : environmentId !== undefined ? "environment" : "global";

  const load = useCallback(async () => {
    const query = { environment_id: environmentId, project_id: projectId };
    const { data } = await api.GET("/api/v1/vault/entries", { params: { query } });
    setEntries(data ?? []);
  }, [environmentId, projectId]);

  useEffect(() => {
    void load();
    void api.GET("/api/v1/vault/kinds").then(({ data }) => setKinds(data ?? []));
  }, [load]);

  const shown = useMemo(
    () =>
      (entries ?? []).filter(
        (entry) =>
          (!kindFilter || entry.kind === kindFilter) &&
          (filter !== "agent" || entry.agent_use) &&
          (filter !== "expiring" || entry.expiry !== null) &&
          (filter !== "unused" || entry.last_used_at === null),
      ),
    [entries, kindFilter, filter],
  );
  // Each kind can be added at its own levels: YAAP's keys per environment, a share password at Global.
  const addable = kinds.filter((kind) => kind.levels.includes(level));

  return (
    <div className="vault">
      <div className="vault-filters">
        <select aria-label="Kind" value={kindFilter} onChange={(event) => setKindFilter(event.target.value)}>
          <option value="">Every kind</option>
          {kinds.map((kind) => (
            <option key={kind.kind} value={kind.kind}>
              {kind.label}
            </option>
          ))}
        </select>
        <div className="vault-filter-buttons" role="group" aria-label="Show">
          {FILTERS.map((item) => (
            <button
              key={item.value}
              type="button"
              className="button button-ghost"
              aria-pressed={filter === item.value}
              onClick={() => setFilter(item.value)}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      {entries === null ? (
        <EmptyState>Loading…</EmptyState>
      ) : shown.length === 0 ? (
        <EmptyState>{entries.length === 0 ? "Nothing in the vault at this level yet." : "Nothing matches."}</EmptyState>
      ) : (
        <div className="wb-table-scroll">
          <table className="wb-table vault-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Kind</th>
                <th>Level</th>
                <th>Value</th>
                <th>Expiry</th>
                <th>Last used</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((entry) => (
                <tr
                  key={entry.id}
                  className={entry.hidden_by_nearer ? "vault-hidden" : undefined}
                  aria-selected={openId === entry.id}
                >
                  <td>
                    <button
                      type="button"
                      className="link-button"
                      aria-expanded={openId === entry.id}
                      onClick={() => setOpenId(openId === entry.id ? null : entry.id)}
                    >
                      {entry.agent_use ? <code>{`{{secret:${entry.name}}}`}</code> : entry.name}
                    </button>{" "}
                    {entry.is_default && <Badge tone="accent">default</Badge>}
                    {entry.hidden_by_nearer && (
                      <Badge title="A nearer level has a secret with this name">overridden</Badge>
                    )}
                  </td>
                  <td>{entry.kind_label}</td>
                  <td>{entry.level === level ? "here" : entry.level_name}</td>
                  <td className="mono">{entry.last_four ? `…${entry.last_four}` : "hidden"}</td>
                  <td>
                    <ExpiryBadge entry={entry} />
                  </td>
                  <td className="small">{moment(entry.last_used_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {openId !== null && (
        <EntryDetail
          key={openId}
          entryId={openId}
          editable={(entries ?? []).find((entry) => entry.id === openId)?.level === level}
          onChanged={load}
          onRemoved={() => {
            setOpenId(null);
            void load();
          }}
        />
      )}

      <AddEntry kinds={addable} environmentId={environmentId} projectId={projectId} onAdded={load} />
    </div>
  );
}

function AddEntry({
  kinds,
  environmentId,
  projectId,
  onAdded,
}: {
  kinds: Kind[];
  environmentId?: number;
  projectId?: number;
  onAdded: () => Promise<void>;
}) {
  const [kind, setKind] = useState("generic");
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [description, setDescription] = useState("");
  const [expires, setExpires] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const chosen = kinds.find((item) => item.kind === kind) ?? kinds[0];

  async function add(event: FormEvent) {
    event.preventDefault();
    if (!chosen) return;
    setBusy(true);
    setError(null);
    const { error: failure } = await api.POST("/api/v1/vault/entries", {
      body: {
        environment_id: environmentId ?? null,
        project_id: projectId ?? null,
        kind: chosen.kind,
        name: name.trim(),
        value,
        description,
        expires_at: expires ? new Date(`${expires}T00:00:00`).toISOString() : null,
      },
    });
    setBusy(false);
    if (failure) {
      setError(errorMessage(failure, "The entry could not be saved."));
      return;
    }
    setName("");
    setValue("");
    setDescription("");
    setExpires("");
    await onAdded();
  }

  if (!chosen) return null;
  return (
    <form className="vault-add" onSubmit={(event) => void add(event)}>
      <h4>Add or replace</h4>
      <p className="faint small">{chosen.description} An entry with the same name at this level gets the new value.</p>
      <div className="vault-add-row">
        <select aria-label="Kind of entry" value={chosen.kind} onChange={(event) => setKind(event.target.value)}>
          {kinds.map((item) => (
            <option key={item.kind} value={item.kind}>
              {item.label}
            </option>
          ))}
        </select>
        <input
          aria-label="Name"
          placeholder={chosen.agent_use ? "NAME, e.g. DB_PASSWORD" : "Name, e.g. personal"}
          value={name}
          maxLength={64}
          onChange={(event) => setName(chosen.agent_use ? event.target.value.toUpperCase() : event.target.value)}
        />
        <input
          aria-label="Value"
          type="password"
          autoComplete="off"
          placeholder="Value"
          value={value}
          onChange={(event) => setValue(event.target.value)}
        />
      </div>
      <div className="vault-add-row">
        <input
          aria-label="Description"
          placeholder="Description (optional): what it is for, where it comes from"
          value={description}
          maxLength={500}
          onChange={(event) => setDescription(event.target.value)}
        />
        <label className="vault-date">
          <span className="faint small">Expires</span>
          <input type="date" value={expires} onChange={(event) => setExpires(event.target.value)} />
        </label>
        <button type="submit" className="button" disabled={busy || !name.trim() || !value}>
          {busy ? (chosen.testable ? "Testing…" : "Saving…") : chosen.testable ? "Test and save" : "Save"}
        </button>
      </div>
      {error && <p className="form-error small">{error}</p>}
    </form>
  );
}

function EntryDetail({
  entryId,
  editable,
  onChanged,
  onRemoved,
}: {
  entryId: number;
  editable: boolean;
  onChanged: () => Promise<void>;
  onRemoved: () => void;
}) {
  const confirm = useConfirm();
  const [detail, setDetail] = useState<Detail | null>(null);
  const [description, setDescription] = useState("");
  const [value, setValue] = useState("");
  const [expires, setExpires] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const { data } = await api.GET("/api/v1/vault/entries/{entry_id}", { params: { path: { entry_id: entryId } } });
    if (!data) return;
    setDetail(data);
    setDescription(data.entry.description);
    setExpires(dateInput(data.entry.expires_at));
  }, [entryId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function update(change: Partial<components["schemas"]["VaultEntryUpdate"]>, done: string) {
    setBusy(true);
    setMessage(null);
    const { error } = await api.PATCH("/api/v1/vault/entries/{entry_id}", {
      params: { path: { entry_id: entryId } },
      body: { clear_expiry: false, make_default: false, ...change },
    });
    setBusy(false);
    setMessage(error ? errorMessage(error, "The change was not saved.") : done);
    if (!error) {
      setValue("");
      await Promise.all([load(), onChanged()]);
    }
  }

  async function test() {
    setBusy(true);
    const { data, error } = await api.POST("/api/v1/vault/entries/{entry_id}/test", {
      params: { path: { entry_id: entryId } },
    });
    setBusy(false);
    setMessage(error ? errorMessage(error, "The test failed.") : (data?.message ?? null));
    await load();
  }

  async function remove() {
    if (!detail) return;
    const question = `Remove ${detail.entry.name} from the vault? Its value cannot be brought back.`;
    if (!(await confirm({ message: question, confirmLabel: "Remove", tone: "danger" }))) return;
    const { error } = await api.DELETE("/api/v1/vault/entries/{entry_id}", { params: { path: { entry_id: entryId } } });
    if (error) {
      setMessage(errorMessage(error, "The entry could not be removed."));
      return;
    }
    onRemoved();
  }

  if (!detail) return null;
  const entry = detail.entry;
  const testable = !entry.agent_use;
  return (
    <section className="vault-detail" aria-label={`Vault entry ${entry.name}`}>
      <header>
        <h4>
          {entry.name} <span className="faint small">· {entry.kind_label} · {entry.level_name}</span>
        </h4>
        {entry.last_test_result && (
          <p className="faint small">
            Last test {moment(entry.last_tested_at)}: {entry.last_test_result}
          </p>
        )}
      </header>

      {!editable && (
        <p className="faint small">This entry belongs to {entry.level_name}. Change it there, or add one with the same name here.</p>
      )}

      {editable && (
        <div className="vault-detail-grid">
          <form
            className="vault-add-row"
            onSubmit={(event) => {
              event.preventDefault();
              void update({ description }, "Description saved.");
            }}
          >
            <input
              aria-label="Description"
              placeholder="Description"
              value={description}
              maxLength={500}
              onChange={(event) => setDescription(event.target.value)}
            />
            <button type="submit" className="button" disabled={busy || description === entry.description}>
              Save
            </button>
          </form>
          <form
            className="vault-add-row"
            onSubmit={(event) => {
              event.preventDefault();
              void update({ value }, testable ? "New value tested and saved." : "New value saved.");
            }}
          >
            <input
              aria-label="New value"
              type="password"
              autoComplete="off"
              placeholder="New value"
              value={value}
              onChange={(event) => setValue(event.target.value)}
            />
            <button type="submit" className="button" disabled={busy || !value}>
              Replace
            </button>
          </form>
          <form
            className="vault-add-row"
            onSubmit={(event) => {
              event.preventDefault();
              void update(
                expires ? { expires_at: new Date(`${expires}T00:00:00`).toISOString() } : { clear_expiry: true },
                expires ? "Expiry saved." : "Expiry removed.",
              );
            }}
          >
            <label className="vault-date">
              <span className="faint small">Expires</span>
              <input type="date" value={expires} onChange={(event) => setExpires(event.target.value)} />
            </label>
            <button type="submit" className="button" disabled={busy || expires === dateInput(entry.expires_at)}>
              Save
            </button>
          </form>
          <div className="vault-add-row">
            {testable && (
              <button type="button" className="button" disabled={busy} onClick={() => void test()}>
                Test
              </button>
            )}
            {!entry.agent_use && !entry.is_default && (
              <button
                type="button"
                className="button"
                disabled={busy}
                onClick={() => void update({ make_default: true }, "This key is now the default.")}
              >
                Make default
              </button>
            )}
            <button type="button" className="button button-ghost danger-link" onClick={() => void remove()}>
              Remove
            </button>
          </div>
        </div>
      )}
      {message && <p className="small">{message}</p>}

      <h5>Used in</h5>
      {detail.used_in.length === 0 ? (
        <p className="faint small">{entry.agent_use ? "Only by name in the agent's commands." : "Not in use."}</p>
      ) : (
        <ul className="small">
          {detail.used_in.map((place) => (
            <li key={place}>{place}</li>
          ))}
        </ul>
      )}

      <h5>Use log</h5>
      {detail.uses.length === 0 ? (
        <p className="faint small">Not used in the last 90 days.</p>
      ) : (
        <table className="wb-table vault-uses">
          <tbody>
            {detail.uses.map((use, index) => (
              <tr key={index}>
                <td className="small">{moment(use.used_at)}</td>
                <td>{use.used_by}</td>
                <td>{use.conversation_id !== null && <a href={`/chat/${use.conversation_id}`}>conversation</a>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
