import type { BlueprintItem } from "../../api/client";
import { type Level, LevelTree, useProjectsByEnvironment } from "../../components/workbench/LevelPicker";
import { RailHeading, RailItem, Segmented } from "../../components/workbench/Workbench";
import { BUILTIN_LAYER, syncMark } from "./blueprintRoute";
import { type ItemKind, KINDS } from "./model";
import type { GitSync } from "./useGitSync";

interface Props {
  level: Level;
  projects: ReturnType<typeof useProjectsByEnvironment>;
  kind: ItemKind;
  view: "list" | "matrix";
  builtinOpen: boolean;
  /** Value of ?builtin= (the open item or the layer marker), kept when the rail entry is clicked again. */
  builtinParam: string | null;
  builtinCount: number | undefined;
  items: BlueprintItem[] | undefined;
  currentName: string;
  sync: GitSync;
  onChooseLevel: (level: Level) => void;
  onChooseKind: (kind: ItemKind) => void;
  onChooseView: (view: "list" | "matrix") => void;
  onOpenBuiltin: (value: string) => void;
}

/** The left rail: level tree with sync marks, push / remote note, kinds, view switch and the Built-in entry. */
export default function BlueprintRail({
  level,
  projects,
  kind,
  view,
  builtinOpen,
  builtinParam,
  builtinCount,
  items,
  currentName,
  sync,
  onChooseLevel,
  onChooseKind,
  onChooseView,
  onOpenBuiltin,
}: Props) {
  const { layerRepository, remoteDraft, setRemoteDraft, syncBusy, syncError, syncAction } = sync;
  const layer = level.kind === "project" ? null : layerRepository(level);
  const layerRepo = layer?.repository;

  return (
    <>
      <RailHeading>Level</RailHeading>
      <LevelTree
        level={level}
        onChange={onChooseLevel}
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
          active={!builtinOpen && entry.kind === kind}
          onClick={() => onChooseKind(entry.kind)}
          count={items ? items.filter((item) => item.kind === entry.kind).length : undefined}
        >
          {entry.title}
        </RailItem>
      ))}
      <RailHeading>View</RailHeading>
      <Segmented
        label="View"
        value={view}
        onChange={(value) => onChooseView(value === "matrix" ? "matrix" : "list")}
        options={[
          { value: "list", label: "List" },
          { value: "matrix", label: "Matrix" },
        ]}
      />
      <RailHeading>Built-in · read-only</RailHeading>
      <RailItem
        active={builtinOpen}
        onClick={() => onOpenBuiltin(builtinParam || BUILTIN_LAYER)}
        count={builtinCount}
        title="Agents and skills shipped with YAAP; change one by copying it to your own layer"
      >
        Built-in
      </RailItem>
    </>
  );
}
