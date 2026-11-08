import { type ReactNode, useEffect, useState } from "react";

import { type Environment, type Project, api } from "../../api/client";
import { useEnvironment } from "../../environment/EnvironmentProvider";
import { LayersIcon } from "../icons";
import { RailItem, Segmented } from "./Workbench";

/** A level of the Blueprint and of settings: global, an environment, or a project in an environment. */
export type Level =
  | { kind: "global" }
  | { kind: "environment"; environmentId: number }
  | { kind: "project"; environmentId: number; projectId: number };

export function levelQuery(level: Level): { environment_id?: number; project_id?: number } {
  if (level.kind === "global") return {};
  if (level.kind === "environment") return { environment_id: level.environmentId };
  return { environment_id: level.environmentId, project_id: level.projectId };
}

export function levelKey(level: Level): string {
  if (level.kind === "global") return "global";
  if (level.kind === "environment") return `environment-${level.environmentId}`;
  return `project-${level.projectId}`;
}

export function sameLevel(a: Level, b: Level): boolean {
  return levelKey(a) === levelKey(b);
}

/** The levels a level inherits from, top first, ending with the level itself. */
export function levelChain(level: Level): Level[] {
  if (level.kind === "global") return [level];
  const environment: Level = { kind: "environment", environmentId: level.environmentId };
  return level.kind === "environment" ? [{ kind: "global" }, environment] : [{ kind: "global" }, environment, level];
}

/** The projects of every environment, for level pickers. */
export function useProjectsByEnvironment(): Record<number, Project[]> {
  const { environments } = useEnvironment();
  const [projects, setProjects] = useState<Record<number, Project[]>>({});
  useEffect(() => {
    let current = true;
    void Promise.all(
      environments.map((environment) =>
        api
          .GET("/api/v1/environments/{environment_id}/projects", { params: { path: { environment_id: environment.id } } })
          .then(({ data }) => [environment.id, data ?? []] as const),
      ),
    ).then((entries) => {
      if (current) setProjects(Object.fromEntries(entries));
    });
    return () => {
      current = false;
    };
  }, [environments]);
  return projects;
}

export function levelName(level: Level, environments: Environment[], projects: Record<number, Project[]>): string {
  if (level.kind === "global") return "Global";
  const environment = environments.find((item) => item.id === level.environmentId);
  if (level.kind === "environment") return environment?.name ?? "Environment";
  return projects[level.environmentId]?.find((item) => item.id === level.projectId)?.name ?? "Project";
}

/** Global, each environment, and optionally its projects, as rail items. */
export function LevelTree({
  level,
  onChange,
  projects,
  includeProjects = true,
  status,
}: {
  level: Level;
  onChange: (level: Level) => void;
  projects: Record<number, Project[]>;
  includeProjects?: boolean;
  /** Something to show at the end of a level's row, such as its sync state. */
  status?: (level: Level) => ReactNode;
}) {
  const { environments } = useEnvironment();
  const global: Level = { kind: "global" };
  return (
    <>
      <RailItem active={sameLevel(level, global)} onClick={() => onChange(global)} count={status?.(global)}>
        <LayersIcon size={14} /> Global
      </RailItem>
      {environments.map((environment) => {
        const environmentLevel: Level = { kind: "environment", environmentId: environment.id };
        return (
          <div key={environment.id} className="wb-level-group">
            <RailItem
              depth={1}
              active={sameLevel(level, environmentLevel)}
              onClick={() => onChange(environmentLevel)}
              count={status?.(environmentLevel)}
            >
              <span className="dot" style={{ background: environment.colour }} />
              {environment.name}
            </RailItem>
            {includeProjects &&
              (projects[environment.id] ?? []).map((project) => {
                const projectLevel: Level = { kind: "project", environmentId: environment.id, projectId: project.id };
                return (
                  <RailItem
                    key={project.id}
                    depth={2}
                    active={sameLevel(level, projectLevel)}
                    onClick={() => onChange(projectLevel)}
                    count={status?.(projectLevel)}
                  >
                    {project.name}
                  </RailItem>
                );
              })}
          </div>
        );
      })}
    </>
  );
}

/** Global and the environments as one segmented switch, for the top of a settings rail. */
export function LevelSwitch({ level, onChange }: { level: Level; onChange: (level: Level) => void }) {
  const { environments } = useEnvironment();
  return (
    <Segmented
      label="Level"
      value={levelKey(level)}
      onChange={(key) => {
        if (key === "global") onChange({ kind: "global" });
        else onChange({ kind: "environment", environmentId: Number(key.replace("environment-", "")) });
      }}
      options={[
        { value: "global", label: "Global" },
        ...environments.map((environment) => ({
          value: `environment-${environment.id}`,
          label: environment.name,
        })),
      ]}
    />
  );
}
