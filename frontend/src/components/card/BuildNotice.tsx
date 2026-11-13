import type { PlanOut } from "../plan/usePlan";

/** Whether the plan's step checklist is worth showing: a queued or cancelled plan has nothing built yet. */
export function showsChecklist(plan: PlanOut): boolean {
  return plan.status !== "queued" && plan.status !== "cancelled";
}

/** The sentence that says why a plan in Build is not building, when it is not. */
function noticeFor(plan: PlanOut): string | null {
  switch (plan.status) {
    case "queued":
      return `Queued: ${plan.build?.queued ?? "waiting for a build slot."}`;
    case "done":
      return "The merge into the version failed; the output is in the card's description.";
    case "cancelled":
      return "The build was stopped before it started.";
    default:
      return null;
  }
}

/** Why a card's plan waits, or what became of it, in a line above the build. */
export default function BuildNotice({ plan }: { plan: PlanOut }) {
  const text = noticeFor(plan);
  return text ? <p className="faint">{text}</p> : null;
}
