import { Link } from "react-router-dom";

import type { UsageSummary } from "./useUsage";

type Budget = NonNullable<UsageSummary["budgets"]>[number];

function BudgetLine({ budget }: { budget: Budget }) {
  const share = budget.limit > 0 ? budget.spent / budget.limit : 1;
  const state = share >= 1 ? "reached" : share >= 0.8 ? "warning" : "ok";
  return (
    <div className={`budget-line budget-${state}`}>
      <span>
        {budget.label}: ${budget.spent.toFixed(2)} of ${budget.limit.toFixed(2)}
      </span>
      <span className="meter" aria-hidden="true">
        <span style={{ width: `${Math.min(100, share * 100)}%` }} />
      </span>
      {state === "reached" && <span>Used up: replies through an API key wait until next month.</span>}
      {state === "warning" && <span>Almost used up.</span>}
    </div>
  );
}

/** This month's usage at the bottom of the sidebar; it opens Settings › Usage and audit › Usage and budget. */
export default function UsageCard({ usage }: { usage: UsageSummary | null }) {
  if (!usage) return null;
  return (
    <Link to="/settings/usage" className="usage-card usage-card-link" title="Usage and budget in Settings">
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <span>Usage this month</span>
        <strong>${usage.cost.toFixed(2)}</strong>
      </div>
      {usage.subscription_records > 0 && (
        <span>
          {usage.subscription_records} {usage.subscription_records === 1 ? "reply" : "replies"} via your
          Claude subscription
        </span>
      )}
      {usage.unpriced_records > 0 && <span>{usage.unpriced_records} reply without a known price</span>}
      {(usage.budgets ?? []).map((budget) => (
        <BudgetLine key={budget.scope} budget={budget} />
      ))}
    </Link>
  );
}
