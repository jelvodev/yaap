import { formatTokens } from "../composer/modelSetup";
import TokenBar from "./TokenBar";
import { TOKEN_KINDS, largestTotal, percent } from "./tokenBars";
import { formatCost } from "./traceOrder";
import { useTokenUsage } from "./useTokenUsage";

interface TokensTabProps {
  conversationId: number;
  onOpenStep: (link: { runId: number; stepId: number }) => void;
}

/** Where the tokens of the whole session went: per reply with its workers and utility calls, and what to change. */
export default function TokensTab({ conversationId, onOpenStep }: TokensTabProps) {
  const { usage, error } = useTokenUsage(conversationId);
  if (error) return <p className="notice notice-error trace-pad">{error}</p>;
  if (!usage) return <p className="faint trace-pad">Loading…</p>;

  const { totals } = usage;
  const hitRate = percent(usage.cache_hit_rate);
  const max = largestTotal([
    ...usage.replies.flatMap((entry) => [entry.reply, ...entry.workers, ...entry.utility]),
    ...usage.other_utility,
  ]);
  const input = totals.cache_read_tokens + totals.cache_write_tokens + totals.input_tokens;
  return (
    <div className="trace-tab-body tokens-tab">
      <p className="tokens-summary">
        {formatTokens(input)} in · {formatTokens(totals.output_tokens)} out
        {totals.cost != null && ` · ${formatCost(totals.cost)}`}
        {hitRate && ` · ${hitRate} of the input from cache`}
      </p>
      <ul className="token-legend faint small">
        {TOKEN_KINDS.map(({ kind, label }) => (
          <li key={kind}>
            <span className={`token-part-${kind} token-swatch`} /> {label}
          </li>
        ))}
      </ul>
      {usage.replies.length === 0 && <p className="faint">Nothing has been used in this conversation yet.</p>}
      {usage.replies.map((entry, index) => (
        <div key={entry.reply.message_id ?? index} className="token-reply">
          <TokenBar row={entry.reply} max={max} />
          {[...entry.workers, ...entry.utility].map((row, rowIndex) => (
            <TokenBar key={`${row.run_id}-${rowIndex}`} row={row} max={max} indent />
          ))}
        </div>
      ))}
      {usage.other_utility.length > 0 && (
        <section>
          <h3>Other calls</h3>
          {usage.other_utility.map((row, index) => (
            <TokenBar key={`${row.label}-${index}`} row={row} max={max} />
          ))}
        </section>
      )}
      {usage.top_tool_results.length > 0 && (
        <section>
          <h3>The ten largest tool results</h3>
          <ul className="token-results">
            {usage.top_tool_results.map((result) => (
              <li key={result.step_id}>
                <button
                  type="button"
                  className="token-result"
                  onClick={() => onOpenStep({ runId: result.run_id, stepId: result.step_id })}
                >
                  <strong>{result.tool}</strong>
                  <span className="token-result-summary">{result.summary}</span>
                  <span className="faint small">{formatTokens(result.tokens)}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      {usage.hints.length > 0 && (
        <section>
          <h3>Where tokens were lost</h3>
          <ul className="token-hints">
            {usage.hints.map((hint) => (
              <li key={hint.kind}>
                {hint.text} <span className="faint">{hint.remedy}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
      <p className="faint small">
        Thinking is counted in the output, as no separate count is stored. The sizes of tool results are estimates.
      </p>
    </div>
  );
}
