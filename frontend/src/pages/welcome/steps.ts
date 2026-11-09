// The steps of the first start and where it resumes. Pure, so it is tested without a browser (steps.test.mjs); its
// only import is of types, which Node strips.

import type { components } from "../../api/schema";

type Steps = components["schemas"]["FirstRunStepsOut"];
export type SetupStep = keyof Steps;
export type WizardStep = "welcome" | SetupStep | "done";

export interface StepInfo {
  step: WizardStep;
  title: string;
}

export const WIZARD_STEPS: readonly StepInfo[] = [
  { step: "welcome", title: "Welcome" },
  { step: "connection", title: "Model connection" },
  { step: "project", title: "Environment and project" },
  { step: "backups", title: "Backups" },
  { step: "starter", title: "YAAP's starter" },
  { step: "done", title: "Done" },
];

export const SETUP_STEPS: readonly SetupStep[] = ["connection", "project", "backups", "starter"];

export function isSetupStep(step: WizardStep): step is SetupStep {
  return (SETUP_STEPS as readonly string[]).includes(step);
}

/** Where the wizard opens: the welcome the first time, else the first step still to do, else the end. */
export function resumeStep(status: { started: boolean; steps: Steps }): WizardStep {
  if (!status.started) return "welcome";
  return SETUP_STEPS.find((step) => status.steps[step] === "todo") ?? "done";
}

/** The step before or after one, staying within the wizard. */
export function neighbour(step: WizardStep, offset: -1 | 1): WizardStep {
  const index = WIZARD_STEPS.findIndex((entry) => entry.step === step);
  const next = Math.min(Math.max(index + offset, 0), WIZARD_STEPS.length - 1);
  return WIZARD_STEPS[next].step;
}
