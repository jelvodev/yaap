import { useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { Level } from "../../components/workbench/LevelPicker";
import {
  Badge,
  EmptyState,
  FieldRow,
  ListGroup,
  ListItem,
  PageHeader,
  Panel,
} from "../../components/workbench/Workbench";
import { type BuiltinItem, type OverriddenBy, builtinFileText, formatCost, itemsOfKind } from "./builtinLogic";
import type { BuiltinItems } from "./useBuiltinItems";

const GUARD_NOTE = "Built in and always on; it cannot be changed or switched off.";

/** Where an item lives: Global, or the environment or project layer named by the server's label. */
export type OverrideLevel = { kind: "global" } | OverriddenBy;

export function BuiltinList({
  builtin,
  selected,
  onSelect,
}: {
  builtin: BuiltinItems;
  selected: string | null;
  onSelect: (name: string) => void;
}) {
  const { items, error } = builtin;
  if (error) return <EmptyState>{error}</EmptyState>;
  if (items === null) return <EmptyState>Loading…</EmptyState>;
  const groups: { title: string; kind: BuiltinItem["kind"] }[] = [
    { title: "Agents", kind: "agent" },
    { title: "Skills", kind: "skill" },
  ];
  return (
    <>
      {groups.map((group) => (
        <div key={group.kind}>
          <ListGroup>{group.title}</ListGroup>
          {itemsOfKind(items, group.kind).map((item) => (
            <ListItem
              key={item.name}
              active={item.name === selected}
              onClick={() => onSelect(item.name)}
              title={item.name}
              badges={item.overridden_by ? <Badge tone="over">changed</Badge> : undefined}
              subtitle={item.description}
            />
          ))}
        </div>
      ))}
    </>
  );
}

/** Read-only detail of one shipped item; "Change in my layer" copies it to Global, where it is edited as any item. */
export function BuiltinDetail({
  builtin,
  selected,
  onOpenItem,
}: {
  builtin: BuiltinItems;
  selected: string | null;
  onOpenItem: (level: OverrideLevel, path: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const item = builtin.items?.find((entry) => entry.name === selected);

  if (!item) {
    return (
      <>
        <PageHeader crumb="Blueprint" title="Built-in" />
        <div className="wb-body wb-body-narrow">
          <EmptyState>
            {builtin.items === null
              ? "Loading…"
              : "These agents and skills ship with YAAP. Choose one on the left to read it."}
          </EmptyState>
        </div>
      </>
    );
  }

  async function changeInMyLayer(target: BuiltinItem) {
    setBusy(true);
    setMessage(null);
    const existing = await api.GET("/api/v1/blueprint/items", { params: { query: {} } });
    if (existing.error) {
      setBusy(false);
      setMessage(errorMessage(existing.error));
      return;
    }
    if (!(existing.data ?? []).some((entry) => entry.path === target.path && entry.defined_here)) {
      const saved = await api.PUT("/api/v1/blueprint/file", {
        params: { query: { path: target.path } },
        body: { content: builtinFileText(target) },
      });
      if (saved.error) {
        setBusy(false);
        setMessage(errorMessage(saved.error));
        return;
      }
    }
    setBusy(false);
    await builtin.reload();
    onOpenItem({ kind: "global" }, target.path);
  }

  const overriddenBy = item.overridden_by;
  return (
    <>
      <PageHeader
        crumb="Blueprint › Built-in"
        title={<span className="mono">{item.name}</span>}
        badges={
          <>
            <Badge>built in</Badge>
            {overriddenBy && <Badge tone="over">changed by you</Badge>}
          </>
        }
        actions={
          item.can_override && (
            <button
              type="button"
              className="button button-primary"
              disabled={busy}
              onClick={() => void changeInMyLayer(item)}
            >
              {busy ? "Working…" : "Change in my layer"}
            </button>
          )
        }
      />
      <div className="wb-body wb-body-narrow">
        {message && <p className="form-error">{message}</p>}
        {!item.can_override && <p className="muted small">{GUARD_NOTE}</p>}
        {overriddenBy && (
          <Panel title={`Changed by you in ${overriddenBy.label}`}>
            <p>
              <button type="button" className="button small" onClick={() => onOpenItem(overriddenBy, item.path)}>
                Open it in {overriddenBy.label}
              </button>
            </p>
            <p className="faint small">Compare it in an eval before you rely on it.</p>
          </Panel>
        )}
        <Panel title="About">
          <FieldRow label="Description">{item.description}</FieldRow>
          <FieldRow label="When it runs">{item.when}</FieldRow>
          {item.role && <FieldRow label="Role">{item.role}</FieldRow>}
          {item.tasks.length > 0 && <FieldRow label="Tasks">{item.tasks.join(", ")}</FieldRow>}
          <FieldRow label="Model">{item.model ?? item.model_note ?? "Chosen at run time"}</FieldRow>
          {item.model && item.model_note && <FieldRow label="Model note">{item.model_note}</FieldRow>}
          <FieldRow label="This month">
            {item.runs === null ? "Not counted" : `${item.runs} run${item.runs === 1 ? "" : "s"}`}
            {item.cost !== null && ` · ${formatCost(item.cost)}`}
          </FieldRow>
        </Panel>
        <Panel title="Instructions" aside={<span className="faint small mono">{item.path}</span>}>
          <pre className="bp-prompt bp-prompt-long">{item.instructions}</pre>
        </Panel>
      </div>
    </>
  );
}

/** The page's level for a layer named by the server; a project is found by its name. */
export function levelOfOverride(
  override: OverrideLevel,
  environmentId: number,
  projects: { id: number; name: string }[],
): Level | null {
  if (override.kind === "global") return { kind: "global" };
  if (override.kind === "environment") return { kind: "environment", environmentId };
  const project = projects.find((entry) => entry.name === override.label);
  return project ? { kind: "project", environmentId, projectId: project.id } : null;
}
