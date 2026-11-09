import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import type { FirstRunControl } from "../../firstRun/useFirstRun";
import { type WizardStep, isSetupStep, neighbour, resumeStep } from "./steps";

/**
 * Which step of the first start is open. It opens where it was left, marks the wizard as started, and keeps the
 * status fresh as the user moves on.
 */
export function useWizard(firstRun: FirstRunControl) {
  const navigate = useNavigate();
  // Run again from Settings (`?again`): open on the welcome, not where an earlier run was left.
  const [params] = useSearchParams();
  const again = params.has("again");
  const [step, setStep] = useState<WizardStep | null>(null);
  const opened = useRef(false);
  const { status, start, reload, skip: skipStep, finish: finishRun } = firstRun;

  useEffect(() => {
    if (!status || opened.current) return;
    opened.current = true;
    setStep(again ? "welcome" : resumeStep(status));
    void start();
  }, [status, start, again]);

  const go = useCallback(
    (next: WizardStep) => {
      setStep(next);
      void reload();
    },
    [reload],
  );

  const move = useCallback((offset: -1 | 1) => step && go(neighbour(step, offset)), [step, go]);

  /** Leave this step for later: it shows as skipped until what it sets up exists. */
  const skip = useCallback(async () => {
    if (!step) return;
    if (isSetupStep(step)) await skipStep(step);
    setStep(neighbour(step, 1));
  }, [step, skipStep]);

  /** Close the wizard for good and go on to a page of the app. */
  const finish = useCallback(
    async (to = "/chat") => {
      await finishRun();
      navigate(to);
    },
    [finishRun, navigate],
  );

  /** Leave the wizard; it comes back on the next visit. */
  const later = useCallback(() => navigate("/chat"), [navigate]);

  return { step, go, next: () => move(1), back: () => move(-1), skip, finish, later };
}
