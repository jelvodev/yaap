export interface BarInput {
  key: string;
  group: string;
  tokens: number;
}

export interface BarSegment {
  key: string;
  group: string;
  percent: number;
}

/**
 * The segments of the context bar in order, each with its width in percent. Widths are relative to the limit when it is
 * known (and larger than what is shown), else to everything shown; negative sizes count as zero, so the sum is at most
 * 100.
 */
export function barSegments(categories: BarInput[], limit: number | null): BarSegment[] {
  const sizes = categories.map((category) => Math.max(0, category.tokens));
  const shown = sizes.reduce((sum, size) => sum + size, 0);
  const whole = limit !== null && limit > shown ? limit : shown;
  return categories.map((category, index) => ({
    key: category.key,
    group: category.group,
    percent: whole > 0 ? (sizes[index] / whole) * 100 : 0,
  }));
}
