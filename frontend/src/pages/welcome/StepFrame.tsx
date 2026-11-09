import type { ReactNode } from "react";

import { PageHeader } from "../../components/workbench/Workbench";
import { WIZARD_STEPS, type WizardStep } from "./steps";

/** One step of the first start: its title, a body that scrolls on its own, and the buttons that move on. */
export default function StepFrame({
  step,
  title,
  children,
  primary,
  onBack,
  onSkip,
  skipLabel = "Skip",
  onLater,
}: {
  step: WizardStep;
  title: ReactNode;
  children: ReactNode;
  /** The main button: Start, Next or Finish. */
  primary: ReactNode;
  onBack?: () => void;
  onSkip?: () => void;
  skipLabel?: string;
  onLater?: () => void;
}) {
  const position = WIZARD_STEPS.findIndex((entry) => entry.step === step) + 1;
  return (
    <div className="welcome-step">
      <PageHeader crumb={`First start · step ${position} of ${WIZARD_STEPS.length}`} title={title} />
      <div className="welcome-body">
        <div className="wb-body wb-body-narrow wb-stack">{children}</div>
      </div>
      <footer className="welcome-footer">
        {onBack && (
          <button type="button" className="button button-ghost" onClick={onBack}>
            Back
          </button>
        )}
        <span className="welcome-footer-gap" />
        {onLater && (
          <button type="button" className="button button-ghost" onClick={onLater}>
            Finish later
          </button>
        )}
        {onSkip && (
          <button type="button" className="button" onClick={onSkip}>
            {skipLabel}
          </button>
        )}
        {primary}
      </footer>
    </div>
  );
}
