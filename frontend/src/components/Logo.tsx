/**
 * The YAAP logo (docs/brand): "> yaap_" on a dark card. The favicon, the y with its cursor, is public/favicon.svg.
 * Drawn as paths, so it looks the same everywhere; the colours are the logo's own, in both themes.
 */

const CARD = "#282a36";
const PROMPT = "#6272a4";
const TEXT = "#f8f8f2";
const CURSOR = "#7aa2f7";

/** The y: a detached left arm that ends in a flat, horizontal cut, and the long stroke into the descender. */
function Y() {
  return (
    <>
      <path d="M346.766 54.242 L353.22 71 L345.72 71 L340.234 56.758 A3.5 3.5 0 0 1 346.766 54.242 Z" fill={TEXT} />
      <path d="M366 55.5 L346.4 103.5" stroke={TEXT} strokeWidth="7" strokeLinecap="round" fill="none" />
    </>
  );
}

// The card around the text: roomy for the new-chat screen, tight for the top bar.
const CARDS = {
  full: { x: 209, y: 17, width: 359, height: 123, radius: 12 },
  compact: { x: 250, y: 21, width: 271, height: 106, radius: 14 },
};

/**
 * The full logo. With `blink`, the prompt blinks like a terminal waiting for input (not with reduced motion);
 * `compact` keeps less room around the text, for small places such as the top bar.
 */
export function Logo({ blink = false, compact = false, className }: { blink?: boolean; compact?: boolean; className?: string }) {
  const card = compact ? CARDS.compact : CARDS.full;
  return (
    <svg className={className} viewBox={`${card.x} ${card.y} ${card.width} ${card.height}`} role="img" aria-label="YAAP">
      <rect x={card.x} y={card.y} width={card.width} height={card.height} rx={card.radius} fill={CARD} />
      <g fill="none" strokeLinecap="round" strokeLinejoin="round">
        <path
          className={blink ? "logo-prompt logo-blink" : "logo-prompt"}
          d="M289 55 L306 70.5 L289 86"
          stroke={PROMPT}
          strokeWidth="7"
        />
        <Y />
        <g stroke={TEXT} strokeWidth="7">
          <ellipse cx="386.5" cy="70.5" rx="7" ry="15" />
          <path d="M393.5 55.5 V82 Q393.5 86 396.5 86" />
          <ellipse cx="416.5" cy="70.5" rx="7" ry="15" />
          <path d="M423.5 55.5 V82 Q423.5 86 426.5 86" />
          <path d="M441.5 55.5 V103.5" />
          <ellipse cx="449.5" cy="70.5" rx="8" ry="15" />
        </g>
      </g>
      <rect x="469" y="89" width="23" height="7" rx="2" fill={CURSOR} />
    </svg>
  );
}
