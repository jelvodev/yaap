import { STAGES, stageIndex } from "./cardLogic";

/** The seven columns of the board in order, the card's own highlighted and the ones behind it marked passed. */
export default function CardStageBar({ column }: { column: string | null }) {
  const current = stageIndex(column);
  return (
    <ol className="card-stages" aria-label="Stage">
      {STAGES.map((stage, index) => (
        <li
          key={stage}
          className="card-stage"
          data-position={index < current ? "passed" : index === current ? "current" : "ahead"}
          aria-current={index === current ? "step" : undefined}
        >
          {stage}
        </li>
      ))}
    </ol>
  );
}
