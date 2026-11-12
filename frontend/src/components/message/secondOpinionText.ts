import type { components } from "../../api/schema";
import { money, tokens } from "../costEstimate.ts";

export type SecondOpinion = components["schemas"]["ReviewOut"];
export type SecondOpinionPreview = components["schemas"]["SecondOpinionPreviewOut"];
type Estimate = components["schemas"]["EstimateOut"];

/** What asking would cost at most: dollars when the model is priced, otherwise the tokens (a subscription). */
export function estimateLine(estimate: Estimate | null): string {
  if (!estimate) return "No estimate available.";
  if (estimate.usd !== null && estimate.usd > 0) {
    return `about ${money(estimate.usd)}`;
  }
  return `subscription: about ${tokens(estimate.input_tokens + estimate.output_tokens)}`;
}

/** What the reviewer looks at, in a few words. */
export function checkedLine(kind: SecondOpinionPreview["kind"]): string {
  return kind === "changes" ? "It reviews the changes this reply made." : "It checks the answer of this reply.";
}

/** The reviewer as the popover names it. */
export function reviewerLine(preview: SecondOpinionPreview): string {
  return `Reviewed by ${preview.reviewer_label ?? preview.reviewer_model ?? "no model"}`;
}

/** The warning for a reviewer of the reply's own vendor; null for any other. */
export function sameVendorLine(preview: SecondOpinionPreview): string | null {
  if (!preview.same_vendor) return null;
  return `Same vendor as the reply: no other vendor is allowed here${preview.reason ? ` — ${preview.reason}` : ""}`;
}

/** The points in a few words: "2 points (1 high)", or "no points". */
export function pointsText(opinion: Pick<SecondOpinion, "findings">): string {
  const count = opinion.findings.length;
  if (count === 0) return "no points";
  const high = opinion.findings.filter((finding) => finding.severity === "high").length;
  return `${count} point${count === 1 ? "" : "s"}${high ? ` (${high} high)` : ""}`;
}

/** A cost in dollars; null when it was free or unknown. */
export function costText(cost: number | null): string | null {
  if (cost === null || cost <= 0) return null;
  return money(cost);
}

/** The collapsed line under a reply. */
export function foldedLine(opinion: SecondOpinion): string {
  if (opinion.status === "running") return "Second opinion · running…";
  if (opinion.status === "skipped") return `Second opinion · skipped${opinion.reason ? `: ${opinion.reason}` : ""}`;
  if (opinion.status === "failed") return `Second opinion · failed${opinion.reason ? `: ${opinion.reason}` : ""}`;
  const verdict = opinion.agrees === null ? null : opinion.agrees ? "agrees" : "disagrees";
  return ["Second opinion", verdict, pointsText(opinion), costText(opinion.cost)].filter(Boolean).join(" · ");
}

/** The text put into the composer, not sent: the opinion for the user to build on. */
export function composerText(opinion: SecondOpinion): string {
  const lines = [`A second opinion (${opinion.reviewer_model ?? "another model"}) on your last reply:`];
  if (opinion.summary) lines.push(opinion.summary);
  for (const finding of opinion.findings) {
    const where = finding.path ? ` ${finding.path}${finding.line !== null ? `:${finding.line}` : ""}:` : "";
    lines.push(`- [${finding.severity}]${where} ${finding.explanation}`);
  }
  if (opinion.alternative) lines.push(`What it would do differently: ${opinion.alternative}`);
  lines.push("Please take this into account.");
  return lines.join("\n");
}
