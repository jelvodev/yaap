/** A choice of a context setting; `value` is what the select holds, `stored` what is saved. */
export interface ContextChoice {
  value: string;
  label: string;
  stored: unknown;
}

// Common context limits in tokens, offered in the chat and in the settings.
export const LIMITS = [64_000, 128_000, 200_000, 256_000, 400_000, 500_000, 750_000, 1_000_000];

export function formatLimit(value: number): string {
  if (value >= 1_000_000) return `${Number((value / 1_000_000).toFixed(2))}M`;
  return `${Math.round(value / 1000)}k`;
}

/** The context settings in the order the screens show them, with their choices. */
export const CONTEXT_KEYS = [
  "context_limit",
  "auto_compact",
  "compact_at",
  "trim_at",
  "repo_map",
  "context_savings",
] as const;

// Shares of the limit, in percent, offered for compact_at and trim_at.
const SHARES = [20, 30, 40, 50, 60, 70, 75, 80, 85, 90];

export function contextChoices(key: string, current: unknown): ContextChoice[] {
  if (key === "context_limit") {
    const sizes = [...LIMITS];
    if (typeof current === "number" && !sizes.includes(current)) sizes.push(current);
    sizes.sort((left, right) => left - right);
    return [
      { value: "window", label: "The model's window", stored: "window" },
      ...sizes.map((size) => ({ value: String(size), label: `${formatLimit(size)} tokens`, stored: size })),
    ];
  }
  if (key === "auto_compact") {
    return [
      { value: "on", label: "On", stored: "on" },
      { value: "off", label: "Off: only /compact and Summarise now", stored: "off" },
    ];
  }
  if (key === "repo_map" || key === "context_savings") {
    return [
      { value: "on", label: "On", stored: "on" },
      { value: "off", label: "Off", stored: "off" },
    ];
  }
  if (key === "compact_at" || key === "trim_at") {
    const shares = [...SHARES];
    if (typeof current === "number" && !shares.includes(current)) shares.push(current);
    shares.sort((left, right) => left - right);
    return shares.map((share) => ({ value: String(share), label: `${share}% of the limit`, stored: share }));
  }
  return [];
}

/** The readable form of a stored value. */
export function contextLabel(key: string, value: unknown): string {
  return contextChoices(key, value).find((choice) => choice.value === String(value))?.label ?? String(value ?? "");
}
