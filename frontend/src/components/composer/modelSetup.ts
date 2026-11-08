/**
 * The pure parts of the message box's controls: the model setup button's label, how full the
 * context is, and the text Goal mode puts in the box. Kept free of React and imports so `modelSetup.test.mjs` can load it.
 */

/** How full the context is: below 80% fine, from 80% filling, from 95% full (as the context meter always had). */
export type ContextState = "ok" | "filling" | "full";

export interface ContextUse {
  /** The tokens of the last reply's context, 0 before the first reply. */
  tokens: number;
  /** The limit YAAP keeps the conversation within, else the model's window; null when neither is known. */
  limit: number | null;
  /** The model's window, when known. */
  window: number | null;
  /** The limit is lower than the window, so both are shown. */
  lowerThanWindow: boolean;
  /** The share of the limit in use, from 0 to 1. */
  fraction: number;
  state: ContextState;
}

/** "33k", "1.0M": a token count as the context meter shows it. */
export function formatTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1000) return `${Math.round(value / 1000)}k`;
  return String(value);
}

/** The context in use against the limit (or the window without one), with the meter's colour state. */
export function contextUse(tokens: number | null, limit: number | null, window: number | null): ContextUse {
  const shownLimit = limit ?? window;
  const used = tokens ?? 0;
  const fraction = used > 0 && shownLimit ? Math.min(1, used / shownLimit) : 0;
  const state: ContextState = fraction >= 0.95 ? "full" : fraction >= 0.8 ? "filling" : "ok";
  return {
    tokens: used,
    limit: shownLimit,
    window,
    lowerThanWindow: Boolean(shownLimit && window && shownLimit < window),
    fraction,
    state,
  };
}

/** "33k / 500k of 1.0M": the window only when the limit is lower. */
export function contextText(use: ContextUse): string {
  const main = `${formatTokens(use.tokens)} / ${formatTokens(use.limit ?? 0)}`;
  return use.lowerThanWindow ? `${main} of ${formatTokens(use.window ?? 0)}` : main;
}

/**
 * A model's name without the family word every model of its vendor shares: "Claude Opus 5.5" becomes "Opus 5.5".
 * Other names stay whole; without a display name the model id is shown.
 */
export function shortModelName(displayName: string | null | undefined, modelId: string): string {
  const name = (displayName ?? "").trim();
  if (!name) return modelId;
  const short = name.replace(/^Claude\s+/i, "");
  return short || name;
}

/** The button's label: `YAAP · Opus 5.5 · medium`; plain YAAP without an agent, no effort when the model has none. */
export function setupLabel(agent: string | null | undefined, model: string, effort: string | null | undefined): string {
  return [agent?.trim() || "YAAP", model, effort?.trim()].filter(Boolean).join(" · ");
}

/** The SVG arc of the ring: the stroke's dash for a fraction of a circle with radius `radius`. */
export function ringDash(fraction: number, radius: number): { length: number; dash: string } {
  const length = 2 * Math.PI * radius;
  const filled = Math.max(0, Math.min(1, fraction)) * length;
  return { length, dash: `${filled.toFixed(2)} ${length.toFixed(2)}` };
}

/** The message box after Goal mode: `/goal ` before what is typed, once; an objective already typed stays. */
export function goalText(current: string): string {
  const text = current.trimStart();
  if (/^\/goal(\s|$)/.test(text)) return text.startsWith("/goal ") ? text : `/goal ${text.slice(5).trimStart()}`;
  return `/goal ${text}`;
}
