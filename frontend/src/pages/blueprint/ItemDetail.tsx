import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { type BlueprintFile, type BlueprintItem, api, errorMessage } from "../../api/client";
import { PackageOriginPanel } from "../../components/BlueprintPackages";
import { SkillOriginPanel } from "../../components/GitHubSkills";
import { type Level, levelQuery } from "../../components/workbench/LevelPicker";
import { Badge, type BadgeTone, EmptyState, PageHeader, Panel, Tabs } from "../../components/workbench/Workbench";
import { type Agent, AgentRuns, AgentSetup } from "./AgentPanels";
import HistoryTab, { type HistoryTarget } from "./HistoryTab";
import {
  EXTENSION_TEMPLATE,
  type ItemKind,
  type ItemState,
  MAX_RULES,
  ON_DEMAND_TOOL,
  STATE_LABELS,
  type StandardDocument,
  TEMPLATES,
  documentBody,
  estimateTokens,
  itemState,
  kindInfo,
  parseStandard,
  promptText,
  serializeStandard,
  stateLabel,
} from "./model";

/** One level of the chain an item is inherited along, with the item as that level sees it. */
export interface ChainEntry {
  key: string;
  name: string;
  colour?: string;
  current: boolean;
  item: BlueprintItem | undefined;
}

type Tab = "edit" | "history" | "prompt" | "runs";

export const STATE_TONES: Record<ItemState, BadgeTone> = {
  defines: "level",
  overrides: "over",
  extends: "extended",
  orphan: "danger",
  off: "off",
  inherits: "neutral",
  absent: "neutral",
};

/** The badge for an item at the level being viewed. */
export function StateBadge({ item, levelName, colour }: { item: BlueprintItem; levelName: string; colour?: string }) {
  const state = itemState(item);
  if (state === "inherits") {
    return <Badge tone="level" colour={item.source === "global" ? undefined : colour}>{item.source_label}</Badge>;
  }
  const text = state === "defines" ? `${levelName} only` : state === "orphan" ? "orphan extension" : STATE_LABELS[state];
  return (
    <Badge tone={STATE_TONES[state]} colour={state === "defines" ? colour : undefined} title={stateLabel(item, levelName)}>
      {text}
    </Badge>
  );
}

function isStandard(kind: ItemKind): boolean {
  return kind === "standard" || kind === "engineering";
}

function StandardFields({ document, onChange }: { document: StandardDocument; onChange: (next: StandardDocument) => void }) {
  const alwaysTokens = estimateTokens([document.summary, ...document.rules].join("\n"));
  const rules = document.rules.length ? document.rules : [""];
  function setRule(index: number, value: string) {
    onChange({ ...document, rules: rules.map((rule, position) => (position === index ? value : rule)) });
  }
  return (
    <>
      <Panel title="Always in the prompt" aside={<span>~{alwaysTokens} tokens</span>}>
        <label className="bp-field-label" htmlFor="bp-summary">
          Summary
        </label>
        <input
          id="bp-summary"
          className="bp-wide"
          value={document.summary}
          placeholder="One sentence on what this standard covers"
          onChange={(event) => onChange({ ...document, summary: event.target.value })}
        />
        <div className="bp-field-label">
          Rules · {document.rules.filter((rule) => rule.trim()).length} of {MAX_RULES}
        </div>
        <ol className="bp-rules">
          {rules.map((rule, index) => (
            <li key={index}>
              <span className="bp-rule-number">{index + 1}</span>
              <input
                value={rule}
                aria-label={`Rule ${index + 1}`}
                placeholder="A rule that must always be followed"
                onChange={(event) => setRule(index, event.target.value)}
              />
              <button
                type="button"
                className="icon-button bp-rule-remove"
                aria-label={`Remove rule ${index + 1}`}
                onClick={() => onChange({ ...document, rules: rules.filter((_, position) => position !== index) })}
              >
                ×
              </button>
            </li>
          ))}
        </ol>
        {rules.length < MAX_RULES && (
          <button type="button" className="button button-ghost bp-add-rule" onClick={() => onChange({ ...document, rules: [...rules, ""] })}>
            + Add rule
          </button>
        )}
      </Panel>
      <Panel title="Full text" aside={<span>loaded on demand · ~{estimateTokens(document.body)} tokens</span>}>
        <textarea
          className="mono bp-body"
          aria-label="Full text"
          value={document.body}
          spellCheck={false}
          rows={18}
          onChange={(event) => onChange({ ...document, body: event.target.value })}
        />
      </Panel>
    </>
  );
}

function Inheritance({ chain, kind }: { chain: ChainEntry[]; kind: ItemKind }) {
  return (
    <Panel title="Inheritance" flush>
      {chain.map((entry) => {
        const state = itemState(entry.item);
        return (
          <div key={entry.key} className={entry.current ? "bp-chain-row bp-chain-current" : "bp-chain-row"}>
            <span className="bp-chain-level">
              {entry.colour && <span className="dot" style={{ background: entry.colour }} />}
              {entry.name}
            </span>
            {state === "absent" || state === "inherits" ? (
              <span className="faint small">{STATE_LABELS[state]}</span>
            ) : (
              <Badge tone={STATE_TONES[state]} colour={state === "defines" ? entry.colour : undefined}>
                {STATE_LABELS[state]}
              </Badge>
            )}
          </div>
        );
      })}
      {kind === "skill" && (
        <p className="faint small bp-chain-note">
          A level below can replace a skill, or keep it and add to it ("extends"). Additions follow updates of the
          original.
        </p>
      )}
    </Panel>
  );
}

function PromptPreview({ item, content }: { item: BlueprintItem; content: string }) {
  const body = documentBody(content);
  const { always, onDemand } = promptText(item, body);
  const tool = ON_DEMAND_TOOL[item.kind];
  return (
    <div className="wb-stack">
      <Panel title="Always in the system prompt" aside={<span>~{estimateTokens(always)} tokens</span>}>
        {item.disabled ? (
          <p className="faint">Switched off: nothing of this item reaches the prompt at this level.</p>
        ) : (
          <pre className="bp-prompt mono">{always}</pre>
        )}
        <p className="faint small">
          {item.kind === "instructions"
            ? "The general instructions go into the prompt in full, after YAAP's base prompt."
            : item.kind === "agent"
              ? "Listed so the model knows the agent exists. A conversation working as this agent gets the role prompt below as part 5 of the context."
              : item.kind === "command"
                ? "Listed so the model knows the command exists; the template is used when you type /" + item.name + "."
                : "Shown in every conversation of this level and the levels below it."}
        </p>
      </Panel>
      {onDemand && (
        <Panel title={tool ? `Loaded on demand with ${tool}` : "Used when invoked"} aside={<span>~{estimateTokens(onDemand)} tokens</span>}>
          <pre className="bp-prompt mono bp-prompt-long">{onDemand}</pre>
        </Panel>
      )}
    </div>
  );
}

export default function ItemDetail({
  level,
  levelName,
  levelColour,
  path,
  item,
  isNew,
  chain,
  historyTarget,
  agent,
  agentEnvironmentId,
  onChanged,
  onRemoved,
}: {
  level: Level;
  levelName: string;
  levelColour?: string;
  path: string;
  item: BlueprintItem | undefined;
  isNew: ItemKind | null;
  chain: ChainEntry[];
  historyTarget: HistoryTarget | null;
  agent: Agent | undefined;
  agentEnvironmentId: number | null;
  onChanged: () => Promise<void>;
  onRemoved: () => void;
}) {
  const navigate = useNavigate();
  const query = levelQuery(level);
  const kind: ItemKind = isNew ?? item?.kind ?? "standard";
  const [file, setFile] = useState<BlueprintFile | null>(null);
  const [content, setContent] = useState(isNew ? TEMPLATES[isNew] : "");
  const [saved, setSaved] = useState(isNew ? "" : content);
  const [raw, setRaw] = useState(false);
  const [tab, setTab] = useState<Tab>("edit");
  const [historyCount, setHistoryCount] = useState<number | null>(null);
  const [runCount, setRunCount] = useState<number | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // For an inherited skill: replace it, or keep the original and add to it.
  const [skillChoice, setSkillChoice] = useState<"replace" | "extend" | null>(null);

  async function load() {
    const { data, error } = await api.GET("/api/v1/blueprint/file", { params: { query: { ...query, path } } });
    if (data) {
      setFile(data);
      setContent(data.content);
      setSaved(data.content);
    } else setMessage(errorMessage(error));
  }

  useEffect(() => {
    if (!isNew) void load();
  }, [path, isNew]);

  // The counts on the History and Runs tabs, before those tabs are opened.
  useEffect(() => {
    if (isNew || !historyTarget) return;
    void api
      .GET("/api/v1/environments/{environment_id}/git/repositories/{repository_id}/log", {
        params: {
          path: { environment_id: historyTarget.environmentId, repository_id: historyTarget.repositoryId },
          query: { path: historyTarget.path, limit: 100 },
        },
      })
      .then(({ data }) => data && setHistoryCount(data.length));
  }, [isNew, historyTarget?.environmentId, historyTarget?.repositoryId, historyTarget?.path, saved]);

  useEffect(() => {
    if (isNew || kind !== "agent" || agentEnvironmentId === null || !item) return;
    void api
      .GET("/api/v1/environments/{environment_id}/runs", {
        params: { path: { environment_id: agentEnvironmentId }, query: { agent: item.name, sub_runs: false } },
      })
      .then(({ data }) => data && setRunCount(data.length));
  }, [isNew, kind, agentEnvironmentId, item?.name]);

  const parsed = useMemo(() => (isStandard(kind) ? parseStandard(content) : null), [kind, content]);
  const dirty = content !== saved;
  const inheritedOnly = Boolean(file && !file.defined_here && !file.extended_here);
  const canExtend = Boolean(inheritedOnly && file?.kind === "skill" && !file.disabled && file.inherited_content);
  const extending = Boolean(file?.extended_here && !file.orphan_extension) || skillChoice === "extend";
  const showEditor = isNew !== null || !canExtend || skillChoice !== null;

  async function run(action: () => Promise<{ error?: unknown }>, after: "reload" | "removed" = "reload") {
    setBusy(true);
    setMessage(null);
    const { error } = await action();
    setBusy(false);
    if (error) {
      setMessage(errorMessage(error));
      return;
    }
    await onChanged();
    if (after === "removed") onRemoved();
    else {
      setSkillChoice(null);
      await load();
    }
  }

  const save = () => run(() => api.PUT("/api/v1/blueprint/file", { params: { query: { ...query, path } }, body: { content } }));
  const remove = (after: "reload" | "removed") =>
    run(() => api.DELETE("/api/v1/blueprint/file", { params: { query: { ...query, path } } }), after);
  const switchOff = () => run(() => api.POST("/api/v1/blueprint/switch-off", { params: { query: { ...query, path } } }));

  const name = isNew ? "New " + kindInfo(kind).singular : (file?.name ?? item?.name ?? path);
  const shown = file ?? item;
  const saveLabel = skillChoice === "extend" ? `Add in ${levelName}` : inheritedOnly ? `Override in ${levelName}` : isNew ? `Create in ${levelName}` : "Save";

  const actions = (
    <>
      {kind === "agent" && shown && !isNew && agentEnvironmentId !== null && (
        <button
          type="button"
          className="button"
          onClick={() => navigate(`/chat?agent=${encodeURIComponent(shown.name)}`)}
          title="A new conversation that works as this agent"
        >
          Start conversation
        </button>
      )}
      {file?.extended_here && (
        <button type="button" className="button" disabled={busy} onClick={() => void remove("reload")}>
          Remove additions
        </button>
      )}
      {inheritedOnly && !file?.disabled && (
        <button type="button" className="button" disabled={busy} onClick={() => void switchOff()}>
          Switch off in {levelName}
        </button>
      )}
      {file?.defined_here && file.inherited_from && (
        <button type="button" className="button" disabled={busy} onClick={() => void remove("reload")}>
          Reset to {file.inherited_from}
        </button>
      )}
      {file?.defined_here && !file.inherited_from && (
        <button
          type="button"
          className="button button-danger"
          disabled={busy}
          onClick={() => {
            if (window.confirm(`Delete ${path} from ${levelName}? It stays in the git history of this layer.`)) void remove("removed");
          }}
        >
          Delete
        </button>
      )}
      {showEditor && (
        <button type="button" className="button button-primary" disabled={busy || (!dirty && !inheritedOnly && !isNew)} onClick={() => void save()}>
          {busy ? "Saving…" : saveLabel}
        </button>
      )}
    </>
  );

  const tabs: { value: Tab; label: string }[] = [{ value: "edit", label: "Edit" }];
  if (!isNew && kind === "agent" && agentEnvironmentId !== null)
    tabs.push({ value: "runs", label: runCount === null ? "Runs" : `Runs · ${runCount}` });
  if (!isNew && historyTarget) tabs.push({ value: "history", label: historyCount === null ? "History" : `History · ${historyCount}` });
  if (!isNew && shown) tabs.push({ value: "prompt", label: "Used in prompt" });

  return (
    <>
      <PageHeader
        crumb={`${levelName} › ${kindInfo(kind).title}`}
        title={<span className={kind === "instructions" ? undefined : "mono"}>{name}</span>}
        badges={
          <>
            {shown && <StateBadge item={shown} levelName={levelName} colour={levelColour} />}
            {shown && !shown.extended_here && (shown.extended_by ?? []).length > 0 && (
              <Badge tone="extended">extended in {(shown.extended_by ?? []).join(", ")}</Badge>
            )}
            {shown?.origin && (
              <Badge title={`${shown.origin.repository}@${shown.origin.commit.slice(0, 7)}`}>
                GitHub{shown.origin.changed_locally ? " · changed locally" : ""}
              </Badge>
            )}
            {shown?.package && (
              <Badge title={`${shown.package.repository}@${shown.package.commit.slice(0, 7)}`}>
                package {shown.package.name}
                {shown.package.changed_locally ? " · changed locally" : ""}
              </Badge>
            )}
            {dirty && <Badge tone="warn">unsaved</Badge>}
          </>
        }
        actions={actions}
        info={<p>{kindInfo(kind).hint} Each save is a commit in the layer's git repository.</p>}
      />
      {tabs.length > 1 && <Tabs label="Item" tabs={tabs} value={tab} onChange={setTab} />}
      {message && <p className="form-error wb-body" style={{ paddingBottom: 0 }}>{message}</p>}

      {tab === "edit" && (
        <div className="wb-body wb-split">
          <div className="wb-stack">
            {kind === "agent" && agent && <AgentSetup agent={agent} />}
            {file?.origin && (
              <SkillOriginPanel name={file.name} origin={file.origin} query={query} definedHere={file.defined_here} onUpdated={load} />
            )}
            {file?.package && (
              <PackageOriginPanel path={file.path} origin={file.package} query={query} definedHere={file.defined_here} onUpdated={load} />
            )}
            {canExtend && skillChoice === null && (
              <Panel title={`Change this skill in ${levelName}`}>
                <p className="muted small">This skill comes from {file?.source_label}. How should {levelName} change it?</p>
                <div className="form-row">
                  <button
                    type="button"
                    className="button button-primary"
                    onClick={() => {
                      setSkillChoice("extend");
                      setContent(EXTENSION_TEMPLATE);
                    }}
                  >
                    Extend: keep the original and add
                  </button>
                  <button type="button" className="button" onClick={() => setSkillChoice("replace")}>
                    Override: replace it
                  </button>
                </div>
              </Panel>
            )}
            {file?.orphan_extension && (
              <p className="form-error small">
                This file extends a skill that no level above has, so it is not used. Remove it, or turn it into a skill of
                its own by removing <span className="mono">extends: true</span>.
              </p>
            )}
            {inheritedOnly && !extending && showEditor && (
              <p className="muted small">
                This content comes from {file?.source_label}. Saving creates an override in {levelName}; {file?.source_label}{" "}
                itself does not change.
              </p>
            )}
            {extending && file?.inherited_content && (
              <Panel title="The skill as the levels above give it" aside={<span>read-only</span>}>
                <pre className="bp-prompt mono bp-prompt-long">{file.inherited_content}</pre>
                <p className="faint small">What you write below is added after it for {levelName} only, also when the original is updated.</p>
              </Panel>
            )}
            {showEditor &&
              (parsed && !raw ? (
                <StandardFields document={parsed} onChange={(next) => setContent(serializeStandard(next))} />
              ) : (
                <Panel title={extending ? "Additions" : "Content"} aside={<span>~{estimateTokens(content)} tokens</span>}>
                  <textarea
                    className="mono bp-body"
                    aria-label={extending ? "Additions" : "Content"}
                    value={content}
                    spellCheck={false}
                    rows={extending ? 10 : 22}
                    onChange={(event) => setContent(event.target.value)}
                  />
                </Panel>
              ))}
            {showEditor && isStandard(kind) && (
              <div>
                <button type="button" className="button button-ghost small" onClick={() => setRaw((value) => !value)} disabled={!parsed && !raw}>
                  {raw || !parsed ? "Edit as fields" : "Edit the raw file"}
                </button>
                {!parsed && <span className="faint small"> The front matter uses forms the field editor does not handle.</span>}
              </div>
            )}
          </div>
          <div className="wb-stack">
            {chain.length > 0 && <Inheritance chain={chain} kind={kind} />}
            <Panel title="File">
              <p className="mono small bp-path">{path}</p>
              {shown && <p className="faint small">{stateLabel(shown, levelName)}</p>}
            </Panel>
          </div>
        </div>
      )}
      {tab === "history" && historyTarget && (
        <div className="wb-body">
          <HistoryTab target={historyTarget} onCount={setHistoryCount} />
        </div>
      )}
      {tab === "prompt" && shown && (
        <div className="wb-body wb-body-narrow">
          <PromptPreview item={shown} content={content} />
        </div>
      )}
      {tab === "runs" && shown && agentEnvironmentId !== null && (
        <div className="wb-body">
          <AgentRuns environmentId={agentEnvironmentId} agent={shown.name} onCount={setRunCount} />
        </div>
      )}
      {!shown && !isNew && <EmptyState>Loading…</EmptyState>}
    </>
  );
}
