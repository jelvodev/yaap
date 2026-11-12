import { formatTokens } from "../composer/modelSetup";
import { barSegments } from "./contextBar";
import type { Breakdown } from "./useContextBreakdown";
import "./context.css";

export interface StepLink {
  runId: number;
  stepId: number;
}

type Category = Breakdown["categories"][number];

const CLI_HINT = "The CLI's own prompt and history (Claude Code or Codex); YAAP cannot see inside it.";

function percentOf(tokens: number, total: number): string {
  return total > 0 ? `${Math.round((tokens / total) * 100)}%` : "";
}

/** Where the context goes: a bar of the categories and, per category, its size, share and largest items. */
export default function ContextPanel({
  breakdown,
  onOpenStep,
}: {
  breakdown: Breakdown;
  onOpenStep?: (link: StepLink) => void;
}) {
  const segments = barSegments(breakdown.categories, breakdown.limit_tokens);
  const labelOf = new Map<string, string>(breakdown.categories.map((category) => [category.key, category.label]));
  const total = breakdown.limit_tokens ?? breakdown.total_tokens;
  return (
    <div className="context-panel">
      <div className="context-stack" role="img" aria-label="Share of the context per category">
        {segments.map((segment) => (
          <span
            key={segment.key}
            className={`context-seg context-seg-${segment.group} context-seg-${segment.key}`}
            style={{ width: `${segment.percent}%` }}
            title={labelOf.get(segment.key)}
          />
        ))}
      </div>
      <table className="context-table">
        <tbody>
          {breakdown.categories.map((category) => (
            <CategoryRow key={category.key} category={category} total={total} onOpenStep={onOpenStep} />
          ))}
        </tbody>
      </table>
      {breakdown.deferred_tools > 0 && (
        <p className="faint small">{breakdown.deferred_tools} more tools wait for find_tools.</p>
      )}
      <p className="faint small">
        {breakdown.estimated
          ? "The total is exact from the last reply; the split is estimated from length and scaled to it. "
          : "The total is exact from the last reply. "}
        {breakdown.basis === "assembly" &&
          (breakdown.snapshot_gone
            ? "The snapshot of the last call was pruned, so this is built from what would be sent now."
            : "There is no snapshot of the last call, so this is built from what would be sent now.")}
      </p>
    </div>
  );
}

function CategoryRow({
  category,
  total,
  onOpenStep,
}: {
  category: Category;
  total: number;
  onOpenStep?: (link: StepLink) => void;
}) {
  const hint = category.key === "cli_remainder" ? CLI_HINT : undefined;
  const share = category.group === "space" ? "" : percentOf(category.tokens, total);
  const name = (
    <span className="context-name">
      <span className={`context-dot context-seg-${category.group} context-seg-${category.key}`} />
      {category.label}
    </span>
  );
  const figures = (
    <>
      <span className="mono nowrap context-tokens">{formatTokens(category.tokens)}</span>
      <span className="mono nowrap context-share">{share}</span>
    </>
  );
  if (category.items.length === 0) {
    return (
      <tr title={hint}>
        <td className="context-row">
          {name}
          {figures}
        </td>
      </tr>
    );
  }
  const items = [...category.items].sort((a, b) => b.tokens - a.tokens);
  return (
    <tr title={hint}>
      <td>
        <details className="context-details">
          <summary className="context-row">
            {name}
            {figures}
          </summary>
          <ul className="context-items">
            {items.map((item) => (
              <li key={`${item.step_id ?? ""}-${item.label}`}>
                <span className="context-item-label" title={item.label}>
                  {item.label}
                </span>
                <span className="mono nowrap faint">{formatTokens(item.tokens)}</span>
                {onOpenStep && item.run_id != null && item.step_id != null && (
                  <StepButton link={{ runId: item.run_id, stepId: item.step_id }} onOpenStep={onOpenStep} />
                )}
              </li>
            ))}
          </ul>
        </details>
      </td>
    </tr>
  );
}

function StepButton({ link, onOpenStep }: { link: StepLink; onOpenStep: (link: StepLink) => void }) {
  return (
    <button type="button" className="link-button small" onClick={() => onOpenStep(link)}>
      Show in trace
    </button>
  );
}
