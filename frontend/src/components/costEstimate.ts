import type { components } from "../api/schema";

/** An estimate from earlier similar runs: a goal's or a scheduled task's. */
export type CostEstimate = components["schemas"]["CostEstimateOut"];

function money(value: number): string {
  return value < 0.01 ? "under $0.01" : `$${value.toFixed(2)}`;
}

function tokens(value: number): string {
  if (value >= 1_000_000) return `${Number((value / 1_000_000).toFixed(1))}M tokens`;
  return value >= 1000 ? `${Math.round(value / 1000)}k tokens` : `${value} tokens`;
}

/**
 * One line about what a run is likely to cost, from `noun` runs before it ("goals", "runs"): the median and the
 * high value (the 90th percentile), in dollars, or in tokens when the earlier runs cost nothing per call.
 */
export function estimateText(estimate: CostEstimate, noun: string): string {
  const { runs, median_cost, high_cost, median_tokens, high_tokens } = estimate;
  if (median_cost === null || high_cost === null || median_tokens === null || high_tokens === null) {
    return `No estimate yet: it needs ${estimate.min_runs} earlier ${noun} (${runs} so far).`;
  }
  const range =
    high_cost > 0 ? `usually ${money(median_cost)}, up to ${money(high_cost)}` : `usually ${tokens(median_tokens)}, up to ${tokens(high_tokens)}`;
  return `Estimate from ${runs} earlier ${noun}: ${range}.`;
}
