import StarterPanel from "../../components/StarterPanel";
import { Panel } from "../../components/workbench/Workbench";
import { useEnvironment } from "../../environment/EnvironmentProvider";
import ImportPanel from "../blueprint/ImportPanel";

/** YAAP's starter Blueprint: the packs, their review and the install, and bringing a setup from another tool. */
export default function StarterStep({ onChanged }: { onChanged: () => void }) {
  const { active } = useEnvironment();
  return (
    <>
      <Panel title="A Blueprint to start with">
        <p className="muted small">
          Standards, skills, commands and agents written for YAAP: how to plan, test, review and debug, and what to
          check before a change is done. Core is the base; add a pack for your field of work. You review every item
          before it is written, and you can change or switch off anything later in the Blueprint.
        </p>
        <StarterPanel onInstalled={() => onChanged()} />
      </Panel>
      <Panel title="Bring your setup">
        <p className="muted small">
          Already use Claude Code, Codex, Cursor or GitHub Copilot? Bring your instructions, skills, commands and agents
          along.
        </p>
        <ImportPanel
          level={{ kind: "global" }}
          levelName="Global"
          utilityEnvironmentId={active?.id ?? null}
          onImported={() => onChanged()}
        />
      </Panel>
    </>
  );
}
