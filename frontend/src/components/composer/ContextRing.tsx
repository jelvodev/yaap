import { type ContextUse, ringDash } from "./modelSetup";

const SIZE = 20;
const RADIUS = 8.5;

/**
 * The context meter as a ring around the model setup's icon: the arc is the share of the context limit in use, in the
 * accent colour, then the warning colour from 80% and the danger colour from 95% (as the bar meter).
 */
export default function ContextRing({ use }: { use: ContextUse }) {
  const { dash } = ringDash(use.fraction, RADIUS);
  const centre = SIZE / 2;
  return (
    <svg
      className={`context-ring context-ring-${use.state}`}
      width={SIZE}
      height={SIZE}
      viewBox={`0 0 ${SIZE} ${SIZE}`}
      aria-hidden="true"
    >
      <circle className="context-ring-track" cx={centre} cy={centre} r={RADIUS} />
      {use.fraction > 0 && (
        <circle
          className="context-ring-arc"
          cx={centre}
          cy={centre}
          r={RADIUS}
          strokeDasharray={dash}
          transform={`rotate(-90 ${centre} ${centre})`}
        />
      )}
      {/* The model's icon inside the ring: a small spark. */}
      <path className="context-ring-icon" d="M10 5.6 11.1 8.9 14.4 10 11.1 11.1 10 14.4 8.9 11.1 5.6 10 8.9 8.9Z" />
    </svg>
  );
}
