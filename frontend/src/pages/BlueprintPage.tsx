import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { type BlueprintItem, type GitRepository, api, errorMessage } from "../api/client";
import { GitHubInstallPanel } from "../components/GitHubSkills";
import { PlusIcon } from "../components/icons";
import {
  type Level,
  LevelTree,
  levelChain,
  levelKey,
  levelName as nameOfLevel,
  levelQuery,
  useProjectsByEnvironment,
} from "../components/workbench/LevelPicker";
import {
  Badge,
  EmptyState,
  ListGroup,
  ListHead,
  ListItem,
  PageHeader,
  Panel,
  RailHeading,
  RailItem,
  Segmented,
  Workbench,
} from "../components/workbench/Workbench";
import { useEnvironment } from "../environment/EnvironmentProvider";
import { type Agent, type AgentTemplate } from "./blueprint/AgentPanels";
import type { HistoryTarget } from "./blueprint/HistoryTab";
import ImportPanel from "./blueprint/ImportPanel";
import ItemDetail, { type ChainEntry, StateBadge } from "./blueprint/ItemDetail";
import PackagesDetail from "./blueprint/PackagesDetail";
import MatrixView, { type MatrixColumn } from "./blueprint/MatrixView";
import { type ItemKind, KINDS, NAME_PATTERN, kindInfo, pathFor } from "./blueprint/model";
import "./blueprint/blueprint.css";

type Filter = "all" | "here" | "inherited" | "overridden" | "off";
type Main = "item" | "new" | "import" | "github" | "package" | "starter" | "template";

const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "here", label: "This level" },
  { value: "inherited", label: "Inherited" },
  { value: "overridden", label: "Overridden" },
  { value: "off", label: "Off" },
];

function matchesFilter(item: BlueprintItem, filter: Filter): boolean {
  switch (filter) {
    case "here":
      return item.defined_here || item.extended_here;
    case "inherited":
      return !item.defined_here && !item.extended_here;
    case "overridden":
      return item.defined_here && Boolean(item.inherited_from);
    case "off":
      return item.disabled;
    default:
      return true;
  }
}

/** The level a link asks for: ?level=global|environment|project with environment and project ids. */
function levelFromParams(params: URLSearchParams): Level | null {
  const environmentId = Number(params.get("environment"));
  const projectId = Number(params.get("project"));
  switch (params.get("level")) {
    case "global":
      return { kind: "global" };
    case "environment":
      return environmentId ? { kind: "environment", environmentId } : null;
    case "project":
      return environmentId && projectId ? { kind: "project", environmentId, projectId } : null;
    default:
      return null;
  }
}

function levelParams(level: Level): Record<string, string> {
  if (level.kind === "global") return { level: "global" };
  if (level.kind === "environment") return { level: "environment", environment: String(level.environmentId) };
  return { level: "project", environment: String(level.environmentId), project: String(level.projectId) };
}

function kindOfPath(path: string): ItemKind | null {
  if (path === "instructions.md") return "instructions";
  const folder = path.split("/")[0];
  return KINDS.find((entry) => entry.folder === folder && entry.folder !== "")?.kind ?? null;
}

/** Sync state of a repository as a short mark for the rail. */
function syncMark(repository: GitRepository | undefined): { text: string; title: string; warn: boolean } | null {
  if (!repository || repository.error) return null;
  if (repository.changes.length) return { text: `●${repository.changes.length}`, title: `${repository.changes.length} uncommitted`, warn: true };
  if (repository.ahead) return { text: `↑${repository.ahead}`, title: `${repository.ahead} commits not pushed`, warn: true };
  if (repository.behind) return { text: `↓${repository.behind}`, title: `${repository.behind} commits to pull`, warn: true };
  if (!repository.remote_url) return { text: "", title: "No remote", warn: false };
  return { text: "✓", title: "In sync with its remote", warn: false };
}

export default function BlueprintPage() {
  const { environments, active } = useEnvironment();
  const projects = useProjectsByEnvironment();
  const [params, setParams] = useSearchParams();

  const level: Level = useMemo(
    () => levelFromParams(params) ?? (active ? { kind: "environment", environmentId: active.id } : { kind: "global" }),
    [params, active],
  );
  const path = params.get("path");
  const kind: ItemKind = (params.get("kind") as ItemKind | null) ?? (path ? kindOfPath(path) : null) ?? "standard";
  const view = params.get("view") === "matrix" ? "matrix" : "list";
  const filter = (params.get("filter") as Filter | null) ?? "all";

  // Settings links to the starter with ?add=starter.
  const [main, setMain] = useState<Main>(() => (params.get("add") === "starter" ? "starter" : "item"));
  const [newKind, setNewKind] = useState<ItemKind | null>(null);
  const [newName, setNewName] = useState("");
  const [newError, setNewError] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [template, setTemplate] = useState<AgentTemplate | null>(null);
  const [columnItems, setColumnItems] = useState<Record<string, BlueprintItem[]>>({});
  const [repositories, setRepositories] = useState<Record<number, GitRepository[]>>({});
  const [agents, setAgents] = useState<Agent[]>([]);
  const [templates, setTemplates] = useState<AgentTemplate[]>([]);
  const [remoteDraft, setRemoteDraft] = useState<string | null>(null);
  const [syncBusy, setSyncBusy] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // The starter link has done its work once the panel is open; a reload shows the list again.
    if (params.has("add")) update({ add: null }, true);
  }, []);

  // The environment whose layers, projects and agents surround the level; Global is seen from the active one.
  const environmentId = level.kind === "global" ? (active?.id ?? null) : level.environmentId;
  const environment = environments.find((entry) => entry.id === environmentId);
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

  function update(patch: Record<string, string | null>, replace = false) {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(patch)) {
      if (value === null) next.delete(key);
      else next.set(key, value);
    }
    setParams(next, { replace });
  }

  function chooseLevel(next: Level) {
    const nextParams = new URLSearchParams({ ...levelParams(next), kind });
    if (path) nextParams.set("path", path);
    if (view === "matrix") nextParams.set("view", "matrix");
    setParams(nextParams);
    setMain("item");
  }

  // Only the newest load may store its result: an older one can finish later with fewer columns.
  const itemsRequest = useRef(0);
  const reloadItems = useCallback(async () => {
    const request = ++itemsRequest.current;
    const entries = await Promise.all(
      columnLevels.map(async (entry) => {
        const { data } = await api.GET("/api/v1/blueprint/items", { params: { query: levelQuery(entry) } });
        return [levelKey(entry), data ?? []] as const;
      }),
    );
    // The level being viewed may sit outside the columns (a project of another environment is not possible, but be safe).
    if (!columnLevels.some((entry) => levelKey(entry) === levelKey(level))) {
      const { data } = await api.GET("/api/v1/blueprint/items", { params: { query: levelQuery(level) } });
      entries.push([levelKey(level), data ?? []] as const);
    }
    if (request === itemsRequest.current) setColumnItems(Object.fromEntries(entries));
  }, [columnLevels, level]);

  const reloadRepositories = useCallback(async () => {
    const entries = await Promise.all(
      environments.map(async (entry) => {
        const { data } = await api.GET("/api/v1/environments/{environment_id}/git/repositories", {
          params: { path: { environment_id: entry.id } },
        });
        return [entry.id, data ?? []] as const;
      }),
    );
    setRepositories(Object.fromEntries(entries));
  }, [environments]);

  const reloadAgents = useCallback(async () => {
    if (environmentId === null) return;
    const [agentsResult, templatesResult] = await Promise.all([
      api.GET("/api/v1/environments/{environment_id}/agents", { params: { path: { environment_id: environmentId } } }),
      api.GET("/api/v1/agent-templates"),
    ]);
    setAgents(agentsResult.data ?? []);
    setTemplates(templatesResult.data ?? []);
  }, [environmentId]);

  useEffect(() => {
    void reloadItems();
  }, [reloadItems]);

  useEffect(() => {
    void reloadRepositories();
  }, [reloadRepositories]);

  useEffect(() => {
    void reloadAgents();
  }, [reloadAgents]);

  useEffect(() => {
    if (!menuOpen) return;
    function close(event: MouseEvent) {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    }
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [menuOpen]);

  const reloadAll = useCallback(async () => {
    await Promise.all([reloadItems(), reloadRepositories(), reloadAgents()]);
  }, [reloadItems, reloadRepositories, reloadAgents]);

  const items = columnItems[levelKey(level)];
  const kindItems = (items ?? []).filter((item) => item.kind === kind);
  const term = search.trim().toLowerCase();
  const listed = kindItems
    .filter((item) => matchesFilter(item, filter))
    .filter((item) => !term || item.name.toLowerCase().includes(term) || item.summary.toLowerCase().includes(term));
  const selected = path ? items?.find((item) => item.path === path) : undefined;

  // The repository of a level's layer, for sync state and history.
  function layerRepository(entry: Level): { environmentId: number; repository: GitRepository | undefined } | null {
    if (entry.kind === "global") {
      if (environmentId === null) return null;
      return { environmentId, repository: repositories[environmentId]?.find((repo) => repo.id === "blueprint-global") };
    }
    if (entry.kind === "environment") {
      return {
        environmentId: entry.environmentId,
        repository: repositories[entry.environmentId]?.find((repo) => repo.id === "blueprint-environment"),
      };
    }
    return {
      environmentId: entry.environmentId,
      repository: repositories[entry.environmentId]?.find((repo) => repo.id === `project-${entry.projectId}`),
    };
  }

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
    update({ path: itemPath, kind: kindOfPath(itemPath) ?? kind, view: null });
  }

  function startNew(itemKind: ItemKind) {
    setMenuOpen(false);
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

  async function syncAction(action: "push" | "remote") {
    const layer = layerRepository(level);
    if (!layer?.repository) return;
    setSyncBusy(true);
    setSyncError(null);
    const repoPath = { environment_id: layer.environmentId, repository_id: layer.repository.id };
    const { error } =
      action === "push"
        ? await api.POST("/api/v1/environments/{environment_id}/git/repositories/{repository_id}/push", { params: { path: repoPath } })
        : await api.PUT("/api/v1/environments/{environment_id}/git/repositories/{repository_id}/remote", {
            params: { path: repoPath },
            body: { url: remoteDraft ?? "" },
          });
    setSyncBusy(false);
    if (error) setSyncError(errorMessage(error));
    else setRemoteDraft(null);
    await reloadRepositories();
  }

  const layer = level.kind === "project" ? null : layerRepository(level);
  const layerRepo = layer?.repository;

  const rail = (
    <>
      <RailHeading>Level</RailHeading>
      <LevelTree
        level={level}
        onChange={chooseLevel}
        projects={projects}
        status={(entry) => {
          if (entry.kind === "project") return undefined;
          const mark = syncMark(layerRepository(entry)?.repository);
          if (!mark || !mark.text) return undefined;
          return (
            <span className={mark.warn ? "bp-sync bp-sync-warn" : "bp-sync"} title={mark.title}>
              {mark.text}
            </span>
          );
        }}
      />
      {layerRepo && layerRepo.has_commits && (layerRepo.ahead > 0 || !layerRepo.remote_url || remoteDraft !== null) && (
        <div className={layerRepo.ahead > 0 ? "wb-rail-note" : "wb-rail-note bp-note-quiet"}>
          {layerRepo.ahead > 0 ? (
            <b>
              {currentName} · {layerRepo.ahead} commit{layerRepo.ahead === 1 ? "" : "s"} not pushed
            </b>
          ) : (
            <span className="faint">{currentName} has no remote; its history stays on this machine.</span>
          )}
          {remoteDraft !== null ? (
            <form
              className="bp-remote-form"
              onSubmit={(event) => {
                event.preventDefault();
                void syncAction("remote");
              }}
            >
              <input
                value={remoteDraft}
                aria-label="Remote URL"
                placeholder="https://github.com/you/blueprint.git"
                onChange={(event) => setRemoteDraft(event.target.value)}
              />
              <div className="wb-rail-note-actions">
                <button type="submit" className="button button-primary small" disabled={syncBusy || !remoteDraft.trim()}>
                  Save remote
                </button>
                <button type="button" className="button button-ghost small" onClick={() => setRemoteDraft(null)}>
                  Cancel
                </button>
              </div>
            </form>
          ) : (
            <div className="wb-rail-note-actions">
              {!layerRepo.remote_url && (
                <button type="button" className="button small" onClick={() => setRemoteDraft("")}>
                  Set remote
                </button>
              )}
              {layerRepo.ahead > 0 && layerRepo.remote_url && (
                <button type="button" className="button small" disabled={syncBusy} onClick={() => void syncAction("push")}>
                  {syncBusy ? "Pushing…" : "Push"}
                </button>
              )}
            </div>
          )}
          {syncError && <p className="form-error small">{syncError}</p>}
        </div>
      )}
      <RailHeading>Kind</RailHeading>
      {KINDS.map((entry) => (
        <RailItem
          key={entry.kind}
          active={entry.kind === kind}
          onClick={() => {
            setMain("item");
            setNewKind(null);
            update({ kind: entry.kind, path: null, filter: null });
          }}
          count={items ? items.filter((item) => item.kind === entry.kind).length : undefined}
        >
          {entry.title}
        </RailItem>
      ))}
      <RailHeading>View</RailHeading>
      <Segmented
        label="View"
        value={view}
        onChange={(value) => update({ view: value === "matrix" ? "matrix" : null })}
        options={[
          { value: "list", label: "List" },
          { value: "matrix", label: "Matrix" },
        ]}
      />
    </>
  );

  const missingTemplates = templates.filter((entry) => !kindItems.some((item) => item.name === entry.name));

  const list = (
    <>
      <ListHead>
        <input
          value={search}
          placeholder={`Filter ${kindInfo(kind).title.toLowerCase()}`}
          aria-label={`Filter ${kindInfo(kind).title.toLowerCase()}`}
          onChange={(event) => setSearch(event.target.value)}
        />
        <div className="bp-menu" ref={menuRef}>
          <button type="button" className="button button-primary small" aria-expanded={menuOpen} onClick={() => setMenuOpen((value) => !value)}>
            <PlusIcon size={14} /> New
          </button>
          {menuOpen && (
            <div className="bp-menu-list" role="menu">
              <button type="button" role="menuitem" onClick={() => startNew(kind)}>
                New {kindInfo(kind).singular} in {currentName}
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setMenuOpen(false);
                  setMain("import");
                }}
              >
                Import Markdown or zip…
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setMenuOpen(false);
                  setMain("github");
                }}
              >
                Skill from GitHub…
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setMenuOpen(false);
                  setMain("package");
                }}
              >
                Add from a package…
              </button>
            </div>
          )}
        </div>
      </ListHead>
      {newKind && newKind !== "instructions" && (
        <form className="wb-list-head bp-new-form" onSubmit={createItem}>
          <input
            autoFocus
            value={newName}
            aria-label={`Name of the new ${kindInfo(newKind).singular}`}
            placeholder={`${kindInfo(newKind).singular} name, e.g. api-design`}
            onChange={(event) => setNewName(event.target.value)}
          />
          <button type="submit" className="button small">
            Create
          </button>
          <button type="button" className="button button-ghost small" onClick={() => setNewKind(null)}>
            Cancel
          </button>
          {newError && <p className="form-error small bp-full">{newError}</p>}
        </form>
      )}
      <div className="wb-list-head bp-filters" role="group" aria-label="Show">
        {FILTERS.map((entry) => (
          <button
            key={entry.value}
            type="button"
            className="bp-filter"
            aria-pressed={filter === entry.value}
            onClick={() => update({ filter: entry.value === "all" ? null : entry.value }, true)}
          >
            {entry.label}
          </button>
        ))}
      </div>
      {items === undefined && <EmptyState>Loading…</EmptyState>}
      {items !== undefined && listed.length === 0 && (
        <EmptyState>
          {kindItems.length === 0 ? `No ${kindInfo(kind).title.toLowerCase()} at this level yet.` : "Nothing matches this filter."}
        </EmptyState>
      )}
      {listed.map((item) => (
        <ListItem
          key={item.path}
          active={main === "item" && item.path === path}
          muted={item.disabled || item.orphan_extension}
          onClick={() => open(item.path)}
          title={item.kind === "instructions" ? "General instructions" : item.name}
          badges={<StateBadge item={item} levelName={currentName} colour={currentColour} />}
          subtitle={
            [item.summary, item.rules.length ? `${item.rules.length} rule${item.rules.length === 1 ? "" : "s"}` : ""]
              .filter(Boolean)
              .join(" · ") || <span className="faint">No summary</span>
          }
        />
      ))}
      {kind === "agent" && missingTemplates.length > 0 && (
        <>
          <ListGroup>Start from a template</ListGroup>
          {missingTemplates.map((entry) => (
            <ListItem
              key={entry.name}
              active={main === "template" && template?.name === entry.name}
              onClick={() => {
                setTemplate(entry);
                setMain("template");
              }}
              title={entry.name}
              badges={<Badge>template</Badge>}
              subtitle={entry.description}
            />
          ))}
        </>
      )}
    </>
  );

  let detail;
  if (view === "matrix") {
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

  return (
    <Workbench label="Blueprint" rail={rail} list={view === "list" ? list : undefined}>
      {detail}
    </Workbench>
  );
}
