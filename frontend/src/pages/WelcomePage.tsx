import type { Environment } from "../api/client";
import { useConfirm } from "../components/dialog/ConfirmDialog";
import { RailHeading, RailItem, Workbench } from "../components/workbench/Workbench";
import { useEnvironment } from "../environment/EnvironmentProvider";
import { type FirstRun, useFirstRun } from "../firstRun/useFirstRun";
import BackupStep from "./welcome/BackupStep";
import ConnectionStep from "./welcome/ConnectionStep";
import DoneStep from "./welcome/DoneStep";
import ProjectStep from "./welcome/ProjectStep";
import StarterStep from "./welcome/StarterStep";
import StepFrame from "./welcome/StepFrame";
import WelcomeStep from "./welcome/WelcomeStep";
import { WIZARD_STEPS, type WizardStep, isSetupStep } from "./welcome/steps";
import { useWizard } from "./welcome/useWizard";
import "./settings/settings.css";
import "./welcome/welcome.css";

const RAIL_MARKS = { done: "✓", skipped: "skipped", todo: undefined } as const;

/** The body of one step. */
function StepContent({
  step,
  status,
  environment,
  onChanged,
  onFinish,
}: {
  step: WizardStep;
  status: FirstRun;
  environment: Environment;
  onChanged: () => void;
  onFinish: (to: string) => void;
}) {
  switch (step) {
    case "welcome":
      return <WelcomeStep />;
    case "connection":
      return <ConnectionStep environment={environment} state={status.steps.connection} onChanged={onChanged} />;
    case "project":
      return <ProjectStep onChanged={onChanged} />;
    case "backups":
      return <BackupStep onChanged={onChanged} />;
    case "starter":
      return <StarterStep onChanged={onChanged} />;
    case "done":
      return <DoneStep status={status} environment={environment} onFinish={onFinish} />;
  }
}

/**
 * The first start: a model connection, the environment and a first project, backups and YAAP's starter, each of
 * which can be skipped. It opens where it was left and comes back on the next visit until it is finished.
 */
export default function WelcomePage() {
  const { active } = useEnvironment();
  const firstRun = useFirstRun();
  const wizard = useWizard(firstRun);
  const confirm = useConfirm();
  const { status, error } = firstRun;

  if (error) return <p className="page-message form-error">{error}</p>;
  if (!status || !wizard.step || !active) return <p className="page-message muted">Loading…</p>;
  const step = wizard.step;
  const title = WIZARD_STEPS.find((entry) => entry.step === step)?.title ?? "";

  async function skip() {
    if (step === "welcome") {
      const question = "Skip the whole setup? Settings › System › First start runs it again.";
      if (await confirm({ message: question, confirmLabel: "Skip the setup" })) await wizard.finish();
      return;
    }
    if (step === "connection" && status?.steps.connection !== "done") {
      const question = "Without a model connection YAAP cannot answer. Skip it for now?";
      if (!(await confirm({ message: question, confirmLabel: "Skip" }))) return;
    }
    await wizard.skip();
  }

  const primary =
    step === "done" ? (
      <button type="button" className="button button-primary" onClick={() => void wizard.finish()}>
        Finish
      </button>
    ) : (
      <button type="button" className="button button-primary" onClick={wizard.next}>
        {step === "welcome" ? "Start" : "Next"}
      </button>
    );

  const rail = (
    <>
      <RailHeading>First start</RailHeading>
      {WIZARD_STEPS.map((entry) => (
        <RailItem
          key={entry.step}
          active={entry.step === step}
          onClick={() => wizard.go(entry.step)}
          count={isSetupStep(entry.step) ? RAIL_MARKS[status.steps[entry.step]] : undefined}
        >
          {entry.title}
        </RailItem>
      ))}
    </>
  );

  return (
    <Workbench label="First start" rail={rail}>
      <StepFrame
        key={step}
        step={step}
        title={step === "welcome" ? "Welcome to YAAP" : title}
        primary={primary}
        onBack={step === "welcome" ? undefined : wizard.back}
        onSkip={step === "done" ? undefined : () => void skip()}
        skipLabel={step === "welcome" ? "Skip the setup" : "Skip"}
        onLater={step === "done" ? undefined : wizard.later}
      >
        <StepContent
          step={step}
          status={status}
          environment={active}
          onChanged={() => void firstRun.reload()}
          onFinish={(to) => void wizard.finish(to)}
        />
      </StepFrame>
    </Workbench>
  );
}
