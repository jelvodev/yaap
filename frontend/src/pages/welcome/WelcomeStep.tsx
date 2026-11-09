import { Panel } from "../../components/workbench/Workbench";
import { WIZARD_STEPS } from "./steps";

/** What YAAP is, and the steps ahead. */
export default function WelcomeStep() {
  const ahead = WIZARD_STEPS.filter((entry) => entry.step !== "welcome" && entry.step !== "done");
  return (
    <>
      <Panel title="YAAP">
        <p>YAAP is your own AI development environment. It runs on your computer and keeps all in one folder.</p>
        <p>You chat with the model of your choice; it reads and changes your projects with tools you approve.</p>
        <p>The Blueprint holds your standards, skills and agents, for every project or for one.</p>
      </Panel>
      <Panel title="The steps ahead">
        <ol className="welcome-steps">
          {ahead.map((entry) => (
            <li key={entry.step}>{entry.title}</li>
          ))}
        </ol>
        <p className="muted small">
          Each step can be skipped and done later in Settings. Finish later leaves the setup; it comes back next time.
        </p>
      </Panel>
    </>
  );
}
