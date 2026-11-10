import { type FormEvent, useCallback, useEffect, useState } from "react";

import { type EndpointStatus, api } from "../../api/client";
import { useConfirm } from "../../components/dialog/ConfirmDialog";
import { Badge, EmptyState, FieldRow, Panel } from "../../components/workbench/Workbench";
import type { LevelSettings } from "./shared";

const KEY = "openai_endpoints";

interface EndpointEntry {
  name: string;
  base_url: string;
  local: boolean;
}

function entriesOf(value: unknown): EndpointEntry[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item): EndpointEntry[] => {
    if (typeof item !== "object" || item === null) return [];
    const { name, base_url: baseUrl, local } = item as Record<string, unknown>;
    return typeof name === "string" && typeof baseUrl === "string" ? [{ name, base_url: baseUrl, local: local === true }] : [];
  });
}

/** The models of every endpoint as the server last read them, with a way to read them again. */
function useEndpointStatuses(listed: string): {
  statuses: EndpointStatus[];
  refreshing: boolean;
  refresh: () => Promise<void>;
} {
  const [statuses, setStatuses] = useState<EndpointStatus[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  useEffect(() => {
    let current = true;
    void api.GET("/api/v1/openai-endpoints").then(({ data }) => {
      if (current) setStatuses(data ?? []);
    });
    return () => {
      current = false;
    };
  }, [listed]);
  const refresh = useCallback(async () => {
    setRefreshing(true);
    const { data } = await api.POST("/api/v1/openai-endpoints/refresh");
    setRefreshing(false);
    setStatuses(data ?? []);
  }, []);
  return { statuses, refreshing, refresh };
}

/** Add or change one endpoint: its name (the provider and model prefix), its base URL and whether it is local. */
function EndpointForm({
  initial,
  onSubmit,
  onCancel,
}: {
  initial: EndpointEntry | null;
  onSubmit: (entry: EndpointEntry) => Promise<string | null>;
  onCancel?: () => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [baseUrl, setBaseUrl] = useState(initial?.base_url ?? "");
  const [local, setLocal] = useState(initial?.local ?? false);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    const failed = await onSubmit({ name: name.trim(), base_url: baseUrl.trim(), local });
    setBusy(false);
    setProblem(failed);
    if (failed === null && initial === null) {
      setName("");
      setBaseUrl("");
      setLocal(false);
    }
  }

  return (
    <form className="settings-inline-form" onSubmit={(event) => void submit(event)}>
      <input
        aria-label="Endpoint name"
        value={name}
        onChange={(event) => setName(event.target.value)}
        placeholder="openrouter"
        maxLength={31}
        disabled={initial !== null}
      />
      <input
        aria-label="Base URL"
        value={baseUrl}
        onChange={(event) => setBaseUrl(event.target.value)}
        placeholder="https://openrouter.ai/api/v1"
        maxLength={300}
      />
      <label>
        <input type="checkbox" checked={local} onChange={(event) => setLocal(event.target.checked)} /> Local
      </label>
      <button type="submit" className="button button-primary" disabled={busy || !name.trim() || !baseUrl.trim()}>
        {initial === null ? "Add" : "Save"}
      </button>
      {onCancel && (
        <button type="button" className="button" onClick={onCancel}>
          Cancel
        </button>
      )}
      {problem && <p className="settings-help form-error">{problem}</p>}
    </form>
  );
}

function EndpointRow({
  entry,
  status,
  onEdit,
  onRemove,
}: {
  entry: EndpointEntry;
  status: EndpointStatus | undefined;
  onEdit: () => void;
  onRemove: () => void;
}) {
  const models = status?.models ?? [];
  return (
    <FieldRow label={entry.name} hint={entry.base_url}>
      <div className="settings-inline-form">
        {entry.local ? <Badge tone="ok">local</Badge> : <Badge tone="neutral">remote</Badge>}
        <button type="button" className="button" onClick={onEdit}>
          Edit
        </button>
        <button type="button" className="button button-danger" onClick={onRemove}>
          Remove
        </button>
      </div>
      {status?.error && <p className="settings-help form-error">{status.error}</p>}
      {models.length > 0 && (
        <p className="settings-help">
          {models.length} models: {models.map((model) => model.id.slice(entry.name.length + 1)).join(", ")}. They show
          in the model picker as {entry.name}/…; a model without a price is not metered.
        </p>
      )}
      {models.length === 0 && !status?.error && <p className="settings-help">The endpoint lists no models.</p>}
    </FieldRow>
  );
}

/**
 * Settings › Connections › OpenAI-compatible endpoints: servers such as OpenRouter, LM Studio or vLLM, set globally.
 * The list of models is read from the server; keys are not part of an endpoint yet.
 */
export default function EndpointsPanel({ store }: { store: LevelSettings }) {
  const confirm = useConfirm();
  const setting = store.find(KEY);
  const entries = entriesOf(setting?.value);
  const { statuses, refreshing, refresh } = useEndpointStatuses(JSON.stringify(entries));
  const [editing, setEditing] = useState<string | null>(null);

  if (!setting) return null;

  async function change(next: EndpointEntry[]): Promise<string | null> {
    return store.save(KEY, next);
  }

  async function remove(entry: EndpointEntry) {
    const question = `Remove the endpoint ${entry.name}? Conversations that use its models stop working until you add it again.`;
    if (await confirm({ message: question, confirmLabel: "Remove", tone: "danger" })) {
      await change(entries.filter((item) => item.name !== entry.name));
    }
  }

  return (
    <Panel
      title="OpenAI-compatible endpoints"
      flush
      aside={
        <button type="button" className="button" onClick={() => void refresh()} disabled={refreshing || entries.length === 0}>
          {refreshing ? "Refreshing…" : "Refresh models"}
        </button>
      }
    >
      {entries.length === 0 && (
        <EmptyState>
          Add a server that speaks the OpenAI Chat Completions API, such as OpenRouter, LM Studio or vLLM. Its models
          run as agents with YAAP's tools. Mark it Local when it runs on your own computer or network.
        </EmptyState>
      )}
      {entries.map((entry) =>
        editing === entry.name ? (
          <FieldRow key={entry.name} label={entry.name}>
            <EndpointForm
              initial={entry}
              onCancel={() => setEditing(null)}
              onSubmit={async (changed) => {
                const failed = await change(entries.map((item) => (item.name === entry.name ? changed : item)));
                if (failed === null) setEditing(null);
                return failed;
              }}
            />
          </FieldRow>
        ) : (
          <EndpointRow
            key={entry.name}
            entry={entry}
            status={statuses.find((status) => status.name === entry.name)}
            onEdit={() => setEditing(entry.name)}
            onRemove={() => void remove(entry)}
          />
        ),
      )}
      <FieldRow label="Add an endpoint" hint="Name, base URL (with /v1), local">
        <EndpointForm initial={null} onSubmit={(added) => change([...entries, added])} />
      </FieldRow>
    </Panel>
  );
}
