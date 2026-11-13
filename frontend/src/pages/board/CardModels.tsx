import type { BoardVersion, CardDetail } from "./boardApi";
import type { Preset } from "./useCardModels";

function usd(value: number): string {
  return `$${value.toFixed(2)}`;
}

function modelText(model: string | null | undefined, effort: string | null | undefined): string {
  if (!model) return "the steering model";
  return effort ? `${model} · ${effort}` : model;
}

/** The models a card's work runs on and what it has cost; read-only, the settings live in the project. */
export default function CardModels({
  card,
  preset,
  version,
}: {
  card: CardDetail;
  preset: Preset | null;
  version: BoardVersion | null;
}) {
  const budget = version?.budget_usd ?? null;
  return (
    <section className="card-detail-section">
      <h3>Models and cost</h3>
      <div className="card-detail-box card-detail-models">
        {preset && (
          <>
            <span>Steers</span>
            <span className="mono">{modelText(preset.model, preset.effort)}</span>
            <span>Builds</span>
            <span className="mono">{modelText(preset.worker_model, preset.worker_effort)}</span>
            <span>Small tasks</span>
            <span className="mono">{preset.utility_model || "the steering model"}</span>
          </>
        )}
        <span>Spent</span>
        <span className="mono">{usd(card.cost_usd)}</span>
        {version && budget !== null && (
          <>
            <span>Version</span>
            <span>
              of the version's {usd(budget)} budget, {usd(version.cost_usd)} spent
            </span>
          </>
        )}
      </div>
    </section>
  );
}
