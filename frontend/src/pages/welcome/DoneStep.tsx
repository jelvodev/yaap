import { useEffect, useState } from "react";

import { type Environment, type Project, api } from "../../api/client";
import { Badge, Panel } from "../../components/workbench/Workbench";
import type { FirstRun, StepState } from "../../firstRun/useFirstRun";
import AutomaticDownloadSwitch from "../../models/AutomaticDownloadSwitch";
import { SETUP_STEPS, WIZARD_STEPS } from "./steps";

const STATE_LABELS: Record<StepState, { tone: "ok" | "off" | "warn"; text: string }> = {
  done: { tone: "ok", text: "set up" },
  skipped: { tone: "off", text: "skipped" },
  todo: { tone: "warn", text: "not set up" },
};

/** What was set up, and three ways to go on; each of them finishes the first start. */
export default function DoneStep({
  status,
  environment,
  onFinish,
}: {
  status: FirstRun;
  environment: Environment;
  onFinish: (to: string) => void;
}) {
  const [project, setProject] = useState<Project | null>(null);

  useEffect(() => {
    let current = true;
    void api
      .GET("/api/v1/environments/{environment_id}/projects", { params: { path: { environment_id: environment.id } } })
      .then(({ data }) => current && setProject(data?.[0] ?? null));
    return () => {
      current = false;
    };
  }, [environment.id]);

  return (
    <>
      <Panel title="What was set up" flush>
        <ul className="welcome-summary">
          {SETUP_STEPS.map((step) => (
            <li key={step}>
              <span>{WIZARD_STEPS.find((entry) => entry.step === step)?.title}</span>
              <Badge tone={STATE_LABELS[status.steps[step]].tone}>{STATE_LABELS[status.steps[step]].text}</Badge>
            </li>
          ))}
        </ul>
      </Panel>
      <Panel title="Search by meaning">
        <AutomaticDownloadSwitch label="Search by meaning: the search model (2.3 GB) downloads in the background" />
        <p className="muted small">
          Until it is there, search finds words only. Settings › System › Models shows the progress.
        </p>
      </Panel>
      <Panel title="Next steps">
        <ul className="welcome-next">
          <li>
            <div>
              <strong>Get to know your project</strong>
              <p className="muted small">
                {project
                  ? `Open ${project.name} and type /onboard: the agent maps the code and proposes what to remember.`
                  : "Create a project in the chat, then type /onboard there."}{" "}
                /onboard comes with YAAP&apos;s starter.
              </p>
            </div>
            <button
              type="button"
              className="button"
              onClick={() => onFinish(project ? `/chat?project=${project.id}` : "/chat")}
            >
              {project ? `Open ${project.name}` : "Open the chat"}
            </button>
          </li>
          <li>
            <div>
              <strong>Look at the Blueprint</strong>
              <p className="muted small">The standards, skills, commands and agents every conversation can use.</p>
            </div>
            <button type="button" className="button" onClick={() => onFinish("/blueprint")}>
              Open the Blueprint
            </button>
          </li>
          <li>
            <div>
              <strong>Start a conversation</strong>
              <p className="muted small">Ask anything, with or without a project.</p>
            </div>
            <button type="button" className="button" onClick={() => onFinish("/chat")}>
              Open the chat
            </button>
          </li>
        </ul>
        <p className="muted small">Settings › System › First start runs this setup again.</p>
      </Panel>
    </>
  );
}
