import { useState } from "react";

import StarterPanel from "../../components/StarterPanel";
import { Panel } from "../../components/workbench/Workbench";
import { useEnvironment } from "../../environment/EnvironmentProvider";
import ImportPanel from "../blueprint/ImportPanel";

/** YAAP's starter Blueprint: the packs, their review and the install, and bringing a setup from another tool. */
export default function StarterStep({ onChanged }: { onChanged: () => void }) {
  const { active } = useEnvironment();
  // A new key after an import clears the review, so the written items cannot be imported a second time by accident.
  const [imports, setImports] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
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
        {notice && <p className="faint small">{notice}</p>}
        <ImportPanel
          key={imports}
          level={{ kind: "global" }}
          levelName="Global"
          utilityEnvironmentId={active?.id ?? null}
          onImported={(written) => {
            setNotice(`Imported ${written.length} item${written.length === 1 ? "" : "s"}: ${written.join(", ")}.`);
            setImports((count) => count + 1);
            onChanged();
          }}
        />
      </Panel>
    </>
  );
}
