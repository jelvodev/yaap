import StarterPanel from "../../components/StarterPanel";
import { Panel } from "../../components/workbench/Workbench";

/** YAAP's starter Blueprint: the packs, their review and the install. */
export default function StarterStep({ onChanged }: { onChanged: () => void }) {
  return (
    <Panel title="A Blueprint to start with">
      <p className="muted small">
        Standards, skills, commands and agents written for YAAP: how to plan, test, review and debug, and what to
        check before a change is done. Core is the base; add a pack for your field of work. You review every item
        before it is written, and you can change or switch off anything later in the Blueprint.
      </p>
      <StarterPanel onInstalled={() => onChanged()} />
    </Panel>
  );
}
