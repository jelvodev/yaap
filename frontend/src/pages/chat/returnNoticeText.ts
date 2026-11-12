/** The words of the notice after a pause; kept apart from the component so they can be tested. */

import type { components } from "../../api/schema";

export type ReturnNoticeFacts = Pick<
  components["schemas"]["ReturnNoticeBodyOut"],
  "cache_lifetime_minutes" | "context_tokens" | "billing" | "cost"
>;

/** An estimated dollar amount with two decimals; below a cent it says so. */
export function formatCost(dollars: number): string {
  return dollars < 0.01 ? "< $0.01" : `$${dollars.toFixed(2)}`;
}

/** The cache lifetime in words: `5 minutes`, `an hour`. */
export function duration(minutes: number): string {
  if (minutes === 60) return "an hour";
  if (minutes % 60 === 0) return `${minutes / 60} hours`;
  return minutes === 1 ? "a minute" : `${minutes} minutes`;
}

/** A token count in thousands: `100k`; small counts stay whole. */
export function formatThousands(tokens: number): string {
  return tokens < 1000 ? String(tokens) : `${Math.round(tokens / 1000)}k`;
}

/** What continuing costs: the cache is written again (metered) or the tokens are read again (subscription). */
export function returnNoticeText(facts: ReturnNoticeFacts): string {
  const idle =
    `This conversation has been idle for over ${duration(facts.cache_lifetime_minutes)}, ` +
    "so the model's cache has expired.";
  const tokens = formatThousands(facts.context_tokens);
  if (facts.billing === "subscription" || facts.cost === null) {
    return `${idle} Continuing reads about ${tokens} tokens again (estimated).`;
  }
  const cost = formatCost(facts.cost);
  return `${idle} Continuing writes its ${tokens} tokens to the cache again: about ${cost} (estimated).`;
}
