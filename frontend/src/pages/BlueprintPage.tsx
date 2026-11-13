import { type FormEvent, useCallback, useMemo, useState } from "react";

import { type BlueprintItem, api, errorMessage } from "../api/client";
import { GitHubInstallPanel } from "../components/GitHubSkills";
import {
  type Level,
  levelChain,
  levelKey,
  levelName as nameOfLevel,
  levelQuery,
  useProjectsByEnvironment,
} from "../components/workbench/LevelPicker";
import { Badge, EmptyState, PageHeader, Panel, Workbench } from "../components/workbench/Workbench";
import { useEnvironment } from "../environment/EnvironmentProvider";
import { type AgentTemplate } from "./blueprint/AgentPanels";
import BlueprintList from "./blueprint/BlueprintList";
import BlueprintRail from "./blueprint/BlueprintRail";
import { BuiltinDetail, BuiltinList, type OverrideLevel, levelOfOverride } from "./blueprint/BuiltinLayer";
import type { HistoryTarget } from "./blueprint/HistoryTab";
import ImportPanel from "./blueprint/ImportPanel";
import ItemDetail, { type ChainEntry } from "./blueprint/ItemDetail";
import PackagesDetail from "./blueprint/PackagesDetail";
import MatrixView, { type MatrixColumn } from "./blueprint/MatrixView";
import { type Main, builtinItemOf, kindOfPath, levelParams, matchesFilter } from "./blueprint/blueprintRoute";
import { type ItemKind, NAME_PATTERN, kindInfo, pathFor } from "./blueprint/model";
import { useBlueprintItems } from "./blueprint/useBlueprintItems";
import { useBlueprintRoute } from "./blueprint/useBlueprintRoute";
import { useBuiltinItems } from "./blueprint/useBuiltinItems";
import { useGitSync } from "./blueprint/useGitSync";
import "./blueprint/blueprint.css";

export default function BlueprintPage() {
  const { environments, active } = useEnvironment();
  const projects = useProjectsByEnvironment();
  const { params, setParams, level, path, kind, view, filter, environmentId, update } = useBlueprintRoute();

  // Settings links to the starter with ?add=starter.
  const [main, setMain] = useState<Main>(() => (params.get("add") === "starter" ? "starter" : "item"));
  const [newKind, setNewKind] = useState<ItemKind | null>(null);
  const [newName, setNewName] = useState("");
  const [newError, setNewError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [template, setTemplate] = useState<AgentTemplate | null>(null);

  const environment = environments.find((entry) => entry.id === environmentId);
  // The read-only Built-in layer is open while ?builtin= is present (`all` for the layer itself); Settings links to it with the item's name.
  const builtinOpen = params.has("builtin");
  const builtin = useBuiltinItems(builtinOpen ? environmentId : null);
  const currentName = nameOfLevel(level, environments, projects);
  const currentColour = level.kind === "global" ? undefined : environments.find((entry) => entry.id === level.environmentId)?.colour;

  const columnLevels: Level[] = useMemo(() => {
    if (environmentId === null) return [{ kind: "global" }];
    return [
      { kind: "global" },
      { kind: "environment", environmentId },
      ...(projects[environmentId] ?? []).map((project) => ({ kind: "project", environmentId, projectId: project.id }) as Level),
    ];
  }, [environmentId, projects]);

  const { columnItems, agents, templates, reloadItems, reloadAgents } = useBlueprintItems(columnLevels, level, environmentId);
  const sync = useGitSync(level, environmentId);
  const { reloadRepositories } = sync;

  const reloadAll = useCallback(async () => {
    await Promise.all([reloadItems(), reloadRepositories(), reloadAgents()]);
  }, [reloadItems, reloadRepositories, reloadAgents]);

  function chooseLevel(next: Level) {
    const nextParams = new URLSearchParams({ ...levelParams(next), kind });
    if (path) nextParams.set("path", path);
    if (view === "matrix") nextParams.set("view", "matrix");
    setParams(nextParams);
    setMain("item");
  }

  const items = columnItems[levelKey(level)];
  const kindItems = (items ?? []).filter((item) => item.kind === kind);
  const term = search.trim().toLowerCase();
  const listed = kindItems
    .filter((item) => matchesFilter(item, filter))
    .filter((item) => !term || item.name.toLowerCase().includes(term) || item.summary.toLowerCase().includes(term));
  const selected = path ? items?.find((item) => item.path === path) : undefined;

  function historyTarget(item: BlueprintItem | undefined): HistoryTarget | null {
    if (!item || environmentId === null) return null;
    const own = item.defined_here || item.extended_here;
    const layer = own ? level.kind : item.source;
    if (layer === "global") return { environmentId, repositoryId: "blueprint-global", path: item.path, label: `Global · ${item.path}` };
    if (layer === "environment") {
      const id = level.kind === "global" ? environmentId : level.environmentId;
      const name = environments.find((entry) => entry.id === id)?.name ?? "Environment";
      return { environmentId: id, repositoryId: "blueprint-environment", path: item.path, label: `${name} · ${item.path}` };
    }
    if (level.kind !== "project") return null;
    return {
      environmentId: level.environmentId,
      repositoryId: `project-${level.projectId}`,
      path: `.blueprint/${item.path}`,
      label: `${currentName} · .blueprint/${item.path}`,
    };
  }

  const chain: ChainEntry[] = path
    ? columnLevels.map((entry) => ({
        key: levelKey(entry),
        name: nameOfLevel(entry, environments, projects),
        colour: entry.kind === "global" ? undefined : environments.find((env) => env.id === entry.environmentId)?.colour,
        current: levelKey(entry) === levelKey(level),
        item: columnItems[levelKey(entry)]?.find((item) => item.path === path),
      }))
    : [];

  const columns: MatrixColumn[] = columnLevels.map((entry) => ({
    key: levelKey(entry),
    name: nameOfLevel(entry, environments, projects),
    colour: entry.kind === "global" ? undefined : environments.find((env) => env.id === entry.environmentId)?.colour,
    current: levelKey(entry) === levelKey(level),
    items: columnItems[levelKey(entry)],
  }));

  function open(itemPath: string) {
    setMain("item");
    update({ path: itemPath, kind: kindOfPath(itemPath) ?? kind, view: null, builtin: null });
  }

  function openFromBuiltin(target: OverrideLevel, itemPath: string) {
    const opened =
      environmentId === null ? null : levelOfOverride(target, environmentId, projects[environmentId] ?? []);
    if (!opened) return;
    setMain("item");
    setParams(new URLSearchParams({ ...levelParams(opened), kind: kindOfPath(itemPath) ?? "agent", path: itemPath }));
  }

  function startNew(itemKind: ItemKind) {
    setNewError(null);
    setNewName("");
    if (itemKind === "instructions") {
      if (items?.some((item) => item.path === "instructions.md")) {
        open("instructions.md");
        return;
      }
      setNewKind("instructions");
      setMain("new");
      update({ path: "instructions.md", kind: "instructions", view: null });
      return;
    }
    setNewKind(itemKind);
  }

  function createItem(event: FormEvent) {
    event.preventDefault();
    if (!newKind) return;
    const name = newName.trim().toLowerCase();
    if (!NAME_PATTERN.test(name)) {
      setNewError("Use lowercase letters, digits and hyphens, for example api-design.");
      return;
    }
    const itemPath = pathFor(newKind, name);
    if (items?.some((item) => item.path === itemPath)) {
      setNewError("That item already exists; open it to override or edit it.");
      return;
    }
    setNewError(null);
    setNewName("");
    setMain("new");
    update({ path: itemPath, kind: newKind, view: null });
  }

  async function addTemplate(name: string) {
    if (environmentId === null) return;
    const { error } = await api.POST("/api/v1/environments/{environment_id}/agents/from-template", {
      params: { path: { environment_id: environmentId } },
      body: { template: name },
    });
    if (error) {
      setNotice(errorMessage(error));
      return;
    }
    setNotice(null);
    setTemplate(null);
    const created = new URLSearchParams({ level: "environment", environment: String(environmentId), kind: "agent", path: `agents/${name}.md` });
    setParams(created);
    setMain("item");
    await reloadAll();
  }

  const rail = (
    <BlueprintRail
      level={level}
      projects={projects}
      kind={kind}
      view={view}
      builtinOpen={builtinOpen}
      builtinParam={params.get("builtin")}
      builtinCount={builtin.items?.length}
      items={items}
      currentName={currentName}
      sync={sync}
      onChooseLevel={chooseLevel}
      onChooseKind={(next) => {
        setMain("item");
        setNewKind(null);
        update({ kind: next, path: null, filter: null, builtin: null });
      }}
      onChooseView={(next) => update({ view: next === "matrix" ? "matrix" : null })}
      onOpenBuiltin={(value) => update({ builtin: value })}
    />
  );

  const list = (
    <BlueprintList
      kind={kind}
      filter={filter}
      main={main}
      path={path}
      items={items}
      kindItems={kindItems}
      listed={listed}
      templates={templates}
      template={template}
      currentName={currentName}
      currentColour={currentColour}
      search={search}
      onSearch={setSearch}
      newKind={newKind}
      newName={newName}
      newError={newError}
      onNewName={setNewName}
      onCancelNew={() => setNewKind(null)}
      onCreate={createItem}
      onStartNew={startNew}
      onChooseMain={setMain}
      onFilter={(next) => update({ filter: next === "all" ? null : next }, true)}
      onOpen={open}
      onOpenTemplate={(entry) => {
        setTemplate(entry);
        setMain("template");
      }}
    />
  );

  let detail;
  if (builtinOpen) {
    detail = <BuiltinDetail builtin={builtin} selected={builtinItemOf(params)} onOpenItem={openFromBuiltin} />;
  } else if (view === "matrix") {
    detail = (
      <>
        <PageHeader
          crumb={`Blueprint · seen from ${environment?.name ?? "Global"}`}
          title={`${kindInfo(kind).title} · matrix`}
          info={<p>Rows are items, columns are levels. Each cell shows whether that level defines, overrides, extends, switches off or inherits the item.</p>}
        />
        <MatrixView kind={kind} columns={columns} onOpen={open} />
      </>
    );
  } else if (main === "import") {
    detail = (
      <>
        <PageHeader crumb={`${currentName} › Import`} title="Import into the Blueprint" />
        <div className="wb-body wb-body-narrow">
          <ImportPanel
            key={levelKey(level)}
            level={level}
            levelName={currentName}
            utilityEnvironmentId={active?.id ?? null}
            items={items ?? []}
            onClose={() => setMain("item")}
            onImported={(written) => {
              setMain("item");
              setNotice(`Imported ${written.length} item${written.length === 1 ? "" : "s"}: ${written.join(", ")}.`);
              void reloadAll();
            }}
          />
        </div>
      </>
    );
  } else if (main === "github") {
    detail = (
      <>
        <PageHeader crumb={`${currentName} › Skills`} title="Skill from GitHub" />
        <div className="wb-body wb-body-narrow">
          <GitHubInstallPanel
            key={levelKey(level)}
            query={levelQuery(level)}
            levelName={currentName}
            onClose={() => setMain("item")}
            onInstalled={(names) => {
              setMain("item");
              setNotice(`Installed ${names.length} skill${names.length === 1 ? "" : "s"}: ${names.join(", ")}.`);
              update({ kind: "skill" });
              void reloadAll();
            }}
          />
        </div>
      </>
    );
  } else if (main === "package" || main === "starter") {
    detail = (
      <PackagesDetail
        key={levelKey(level)}
        source={main === "starter" ? "starter" : "link"}
        levelName={currentName}
        levels={levelChain(level).map((entry) => ({
          key: levelKey(entry),
          name: nameOfLevel(entry, environments, projects),
          query: levelQuery(entry),
        }))}
        initialLevel={levelKey(level)}
        onSource={(source) => setMain(source === "starter" ? "starter" : "package")}
        onClose={() => setMain("item")}
        onInstalled={(paths, target) => {
          setMain("item");
          const count = `${paths.length} item${paths.length === 1 ? "" : "s"}`;
          setNotice(`Installed ${count} in ${target}: ${paths.join(", ")}.`);
          void reloadAll();
        }}
      />
    );
  } else if (main === "template" && template) {
    detail = (
      <>
        <PageHeader
          crumb={`${environment?.name ?? "Environment"} › Agents`}
          title={<span className="mono">{template.name}</span>}
          badges={<Badge>template</Badge>}
          actions={
            <button type="button" className="button button-primary" onClick={() => void addTemplate(template.name)}>
              Add to {environment?.name ?? "this environment"}
            </button>
          }
        />
        <div className="wb-body wb-body-narrow">
          <Panel title="Template">
            <p>{template.description}</p>
            <p className="faint small">
              Adding it writes <span className="mono">agents/{template.name}.md</span> to the {environment?.name} layer, where you can
              change its prompt, preset and tools. A project can still override it.
            </p>
            {notice && <p className="form-error">{notice}</p>}
          </Panel>
        </div>
      </>
    );
  } else if (path && (selected || main === "new")) {
    detail = (
      <ItemDetail
        key={`${levelKey(level)}:${path}:${main}`}
        level={level}
        levelName={currentName}
        levelColour={currentColour}
        path={path}
        item={selected}
        isNew={main === "new" && !selected ? kind : null}
        chain={chain}
        historyTarget={historyTarget(selected)}
        agent={agents.find((agent) => agent.path === path)}
        agentEnvironmentId={environmentId}
        onChanged={async () => {
          setMain("item");
          await reloadAll();
        }}
        onRemoved={(message) => {
          update({ path: null });
          if (message) setNotice(message);
        }}
      />
    );
  } else {
    detail = (
      <>
        <PageHeader
          crumb="Blueprint"
          title={`${kindInfo(kind).title} · ${currentName}`}
          info={
            <>
              <p>
                {level.kind === "global"
                  ? "Shared by every environment and project."
                  : `Inherits from the levels above. Override, extend, add or switch off items for ${currentName} only.`}{" "}
                Each layer is a git repository; every save attempts a path-scoped commit.
              </p>
              <p>{kindInfo(kind).hint}</p>
            </>
          }
        />
        <div className="wb-body wb-body-narrow">
          {notice && <p className="muted small">{notice}</p>}
          <EmptyState>
            {items === undefined
              ? "Loading…"
              : kindItems.length
                ? `Choose one of the ${kindItems.length} ${kindInfo(kind).title.toLowerCase()} on the left, or create a new one.`
                : `There are no ${kindInfo(kind).title.toLowerCase()} at this level yet. Use New to create or import one.`}
          </EmptyState>
        </div>
      </>
    );
  }

  const builtinList = (
    <BuiltinList
      builtin={builtin}
      selected={builtinItemOf(params)}
      onSelect={(name) => update({ builtin: name })}
    />
  );

  return (
    <Workbench label="Blueprint" rail={rail} list={builtinOpen ? builtinList : view === "list" ? list : undefined}>
      {detail}
    </Workbench>
  );
}
