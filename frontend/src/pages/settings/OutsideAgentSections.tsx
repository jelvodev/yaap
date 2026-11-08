import A2aAgentsPanel from "../../components/A2aAgentsPanel";
import McpServersPanel from "../../components/McpServersPanel";
import { Panel } from "../../components/workbench/Workbench";
import { type SettingsLevel, SectionHeader } from "./shared";

type EnvironmentLevel = Extract<SettingsLevel, { kind: "environment" }>;

/** Connections › MCP servers: outside tools for the agent, per environment; a project can add its own. */
export function McpServersSection({ level }: { level: EnvironmentLevel }) {
  return (
    <>
      <SectionHeader
        level={level}
        title="MCP servers"
        info={
          <p>
            Outside tools for the agent in {level.environment.name}, through the Model Context Protocol. Claude Code
            and Codex get them through YAAP. A project can add its own in its settings.
          </p>
        }
      />
      <div className="wb-body wb-body-narrow wb-stack">
        <Panel title="MCP servers" aside="Outside tools for the agent; Claude Code and Codex get them through YAAP">
          <McpServersPanel environmentId={level.environment.id} />
        </Panel>
      </div>
    </>
  );
}

/** Connections › A2A agents: other agents YAAP's agent may ask, per environment. */
export function A2aAgentsSection({ level }: { level: EnvironmentLevel }) {
  return (
    <>
      <SectionHeader
        level={level}
        title="A2A agents"
        info={
          <p>
            Other agents the agent in {level.environment.name} may ask over the A2A protocol, with a test that reads
            each agent&apos;s card. Every call asks first.
          </p>
        }
      />
      <div className="wb-body wb-body-narrow wb-stack">
        <Panel title="A2A agents" aside="Other agents YAAP may ask; each call asks first">
          <A2aAgentsPanel environmentId={level.environment.id} />
        </Panel>
      </div>
    </>
  );
}
