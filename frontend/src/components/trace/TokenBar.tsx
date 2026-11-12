import { formatTokens } from "../composer/modelSetup";
import { type TokenRow, tokenSegments, rowTotal } from "./tokenBars";
import { formatCost } from "./traceOrder";

/** One row of the token overview: its label, a stacked bar scaled to `max`, its tokens and its cost. */
export default function TokenBar({ row, max, indent = false }: { row: TokenRow; max: number; indent?: boolean }) {
  const parts = tokenSegments(row, max);
  const detail = parts.map((part) => `${part.label}: ${part.tokens.toLocaleString()}`).join("\n");
  return (
    <div className={`token-row${indent ? " token-row-indent" : ""}`}>
      <span className="token-row-label" title={row.label}>
        {row.label}
      </span>
      <div className="token-bar" title={detail}>
        {parts.map((part) => (
          <span key={part.kind} className={`token-part token-part-${part.kind}`} style={{ width: `${part.width}%` }} />
        ))}
      </div>
      <span className="faint small token-row-total">{formatTokens(rowTotal(row))}</span>
      <span className="faint small token-row-cost">{row.cost != null ? formatCost(row.cost) : ""}</span>
    </div>
  );
}
