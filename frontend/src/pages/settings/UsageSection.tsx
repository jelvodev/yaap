import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";
import { Badge, EmptyState, FieldRow, Panel, Segmented } from "../../components/workbench/Workbench";
import { type LevelSettings, type SettingsLevel, SectionHeader, TextSetting, isOwn } from "./shared";

type Report = components["schemas"]["UsageReportOut"];
type Group = components["schemas"]["UsageGroupOut"];
type Totals = components["schemas"]["UsageTotalsOut"];
type Budget = components["schemas"]["BudgetOut"];

const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function money(value: number): string {
  return value >= 100 ? `$${value.toFixed(0)}` : `$${value.toFixed(2)}`;
}

function tokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(0)}k`;
  return String(value);
}

function monthLabel(key: string, long = false): string {
  const [year, month] = key.split("-").map(Number);
  return long ? `${MONTH_NAMES[month - 1]} ${year}` : MONTH_NAMES[month - 1];
}

function inputTokens(totals: Totals): number {
  return totals.input_tokens + totals.cache_read_tokens + totals.cache_write_tokens;
}

function amount(value: unknown): number | null {
  const text = String(value ?? "").replace(",", ".").replace("$", "").trim();
  if (!text) return null;
  const number = Number(text);
  return Number.isFinite(number) ? number : null;
}

function currentMonthKey(): string {
  const now = new Date();
  return `${now.getUTCFullYear()}-${String(now.getUTCMonth() + 1).padStart(2, "0")}`;
}

function budgetState(budget: Budget): "ok" | "warning" | "reached" {
  const share = budget.limit > 0 ? budget.spent / budget.limit : 1;
  return share >= 1 ? "reached" : share >= 0.8 ? "warning" : "ok";
}

/** Charged and subscription value per month, stacked; a bar selects its month. */
function MonthChart({ months, selected, onSelect }: { months: Group[]; selected: string; onSelect: (key: string) => void }) {
  const [hover, setHover] = useState<string | null>(null);
  const highest = Math.max(...months.map((month) => month.totals.cost + month.totals.subscription_value), 0);
  const scale = highest > 0 ? highest : 1;
  const shown = months.find((month) => month.key === (hover ?? selected));
  return (
    <Panel
      title="Per month"
      aside={
        <span className="usage-legend">
          <span>
            <i className="swatch swatch-charged" /> Charged (API)
          </span>
          <span>
            <i className="swatch swatch-subscription" /> Subscription value
          </span>
        </span>
      }
    >
      <p className="muted small usage-chart-readout">
        {shown
          ? `${monthLabel(shown.key, true)}: ${money(shown.totals.cost)} charged · ${money(shown.totals.subscription_value)} subscription value · ${shown.totals.calls} calls`
          : " "}
      </p>
      <div className="usage-chart settings-usage-chart" role="group" aria-label="Usage per month">
        {months.map((month) => {
          const charged = (month.totals.cost / scale) * 100;
          const value = (month.totals.subscription_value / scale) * 100;
          return (
            <button
              key={month.key}
              type="button"
              className="usage-bar"
              aria-pressed={month.key === selected}
              aria-label={`${monthLabel(month.key, true)}: ${money(month.totals.cost)} charged, ${money(month.totals.subscription_value)} subscription value`}
              onMouseEnter={() => setHover(month.key)}
              onMouseLeave={() => setHover(null)}
              onFocus={() => setHover(month.key)}
              onBlur={() => setHover(null)}
              onClick={() => onSelect(month.key)}
            >
              <span className="usage-bar-track">
                {value > 0 && <span className="usage-segment segment-subscription" style={{ height: `${value}%` }} />}
                {charged > 0 && <span className="usage-segment segment-charged" style={{ height: `${charged}%` }} />}
              </span>
              <span className="usage-bar-label">{monthLabel(month.key)}</span>
            </button>
          );
        })}
      </div>
    </Panel>
  );
}

/** One budget with how much of it is used this month, and a warning when it is almost or fully used. */
function BudgetMeter({ budget }: { budget: Budget }) {
  const state = budgetState(budget);
  const share = budget.limit > 0 ? Math.min(1, budget.spent / budget.limit) : 1;
  return (
    <div className={`settings-budget settings-budget-${state}`}>
      <div className="settings-budget-line">
        <span>{budget.label}</span>
        <span className="settings-budget-amount">
          {money(budget.spent)} of {money(budget.limit)}
        </span>
      </div>
      <div className="wb-bar" aria-hidden="true">
        <i style={{ width: `${share * 100}%` }} />
      </div>
      {state === "reached" && <p className="settings-help">Used up: replies through an API key wait until next month.</p>}
      {state === "warning" && <p className="settings-help">Almost used up.</p>}
    </div>
  );
}

type Breakdown = "place" | "provider" | "model" | "purpose" | "billing" | "conversations";

function BreakdownPanel({ report, level }: { report: Report; level: SettingsLevel }) {
  const [breakdown, setBreakdown] = useState<Breakdown>("place");
  const inEnvironment = level.kind === "environment";
  const options: { value: Breakdown; label: string; groups: Group[]; column: string }[] = [
    inEnvironment
      ? { value: "place", label: "Project", groups: report.by_project, column: "Project" }
      : { value: "place", label: "Environment", groups: report.by_environment, column: "Environment" },
    { value: "provider", label: "Provider", groups: report.by_provider, column: "Provider" },
    { value: "model", label: "Model", groups: report.by_model, column: "Model" },
    { value: "purpose", label: "Role", groups: report.by_purpose, column: "Role" },
    { value: "billing", label: "Billing", groups: report.by_billing, column: "Billing" },
    { value: "conversations", label: "Top conversations", groups: report.top_conversations, column: "Conversation" },
  ];
  const current = options.find((option) => option.value === breakdown) ?? options[0];
  const weight = (group: Group) => group.totals.cost + group.totals.subscription_value;
  const total = current.groups.reduce((sum, group) => sum + weight(group), 0);
  // Conversations open in their own environment, so links only make sense within one.
  const link = current.value === "conversations" && inEnvironment ? (group: Group) => `/chat/${group.key}` : undefined;

  return (
    <Panel
      title="Breakdown"
      flush
      aside={
        <Segmented
          label="Break down by"
          value={breakdown}
          onChange={setBreakdown}
          options={options.map((option) => ({ value: option.value, label: option.label }))}
        />
      }
    >
      {current.groups.length === 0 ? (
        <EmptyState>No usage in {monthLabel(report.month, true)}.</EmptyState>
      ) : (
        <div className="wb-table-scroll settings-scroll">
          <table className="wb-table">
            <thead>
              <tr>
                <th scope="col">{current.column}</th>
                <th scope="col" className="wb-num">
                  Calls
                </th>
                <th scope="col" className="wb-num">
                  Input
                </th>
                <th scope="col" className="wb-num">
                  Output
                </th>
                <th scope="col" className="wb-num">
                  Charged
                </th>
                <th scope="col" className="wb-num">
                  Subscription value
                </th>
                <th scope="col" className="settings-share-column">
                  Share
                </th>
              </tr>
            </thead>
            <tbody>
              {current.groups.map((group) => (
                <tr key={group.key}>
                  <td>{link ? <Link to={link(group)}>{group.label}</Link> : group.label}</td>
                  <td className="wb-num">{group.totals.calls}</td>
                  <td className="wb-num">{tokens(inputTokens(group.totals))}</td>
                  <td className="wb-num">{tokens(group.totals.output_tokens)}</td>
                  <td className="wb-num">
                    {money(group.totals.cost)}
                    {group.totals.unpriced_calls > 0 && <span className="faint"> +{group.totals.unpriced_calls} unpriced</span>}
                  </td>
                  <td className="wb-num">
                    {group.totals.subscription_value > 0 ? money(group.totals.subscription_value) : "–"}
                  </td>
                  <td>
                    <div className="wb-bar" title={`${total > 0 ? Math.round((weight(group) / total) * 100) : 0}%`}>
                      <i style={{ width: `${total > 0 ? (weight(group) / total) * 100 : 0}%` }} />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

/** What a level used per month, its budget, and a breakdown. Global shows all environments together. */
export default function UsageSection({ level, store }: { level: SettingsLevel; store: LevelSettings }) {
  const navigate = useNavigate();
  const environmentId = level.kind === "environment" ? level.environment.id : null;
  const [month, setMonth] = useState<string | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [budgets, setBudgets] = useState<Budget[]>([]);

  useEffect(() => {
    setMonth(null);
  }, [environmentId]);

  useEffect(() => {
    let current = true;
    const query = { month: month ?? undefined };
    const request =
      environmentId === null
        ? api.GET("/api/v1/usage/report", { params: { query } })
        : api.GET("/api/v1/environments/{environment_id}/usage/report", {
            params: { path: { environment_id: environmentId }, query },
          });
    void request.then(({ data, error: failure }) => {
      if (!current) return;
      if (failure || !data) setError(errorMessage(failure, "The usage could not be loaded."));
      else {
        setError(null);
        setReport(data);
      }
    });
    return () => {
      current = false;
    };
  }, [environmentId, month]);

  // The budgets of this month, as the chat shows them; they follow a change of the budget settings.
  const budgetSettings = `${String(store.find("budget_environment")?.value ?? "")}|${String(store.findGlobal("budget_total")?.value ?? "")}`;
  useEffect(() => {
    if (environmentId === null) {
      setBudgets([]);
      return;
    }
    let current = true;
    void api
      .GET("/api/v1/environments/{environment_id}/usage", { params: { path: { environment_id: environmentId } } })
      .then(({ data }) => current && setBudgets(data?.budgets ?? []));
    return () => {
      current = false;
    };
  }, [environmentId, budgetSettings]);

  const totalSetting = store.findGlobal("budget_total");
  const environmentSetting = level.kind === "environment" ? store.find("budget_environment") : undefined;
  const thisMonth = report?.months.find((item) => item.key === currentMonthKey());

  // At Global the total budget is measured against what all environments spent this month.
  const totalLimit = amount(totalSetting?.value);
  const shownBudgets: Budget[] =
    level.kind === "environment"
      ? budgets
      : totalLimit !== null
        ? [{ scope: "total", label: "All environments", limit: totalLimit, spent: thisMonth?.totals.cost ?? 0 }]
        : [];
  const worst = shownBudgets.find((budget) => budgetState(budget) === "reached") ?? shownBudgets.find((budget) => budgetState(budget) === "warning");
  const ownLimit = level.kind === "environment" ? amount(environmentSetting?.value) : totalLimit;

  const totals = report?.totals;
  const utilityCalls = report?.by_purpose.find((group) => group.key === "utility")?.totals.calls ?? 0;
  const cacheShare = totals && inputTokens(totals) > 0 ? Math.round((totals.cache_read_tokens / inputTokens(totals)) * 100) : 0;
  const monthKeys = report ? [...report.months.map((item) => item.key)].reverse() : [];

  return (
    <>
      <SectionHeader
        level={level}
        title="Usage and budget"
        badges={
          worst && (
            <Badge tone={budgetState(worst) === "reached" ? "danger" : "warn"}>
              {budgetState(worst) === "reached" ? "budget used up" : "budget almost used up"}
            </Badge>
          )
        }
        actions={
          report && (
            <>
              <label className="visually-hidden" htmlFor="usage-month">
                Month
              </label>
              <select id="usage-month" value={report.month} onChange={(event) => setMonth(event.target.value)}>
                {monthKeys.map((key) => (
                  <option key={key} value={key}>
                    {monthLabel(key, true)}
                  </option>
                ))}
              </select>
            </>
          )
        }
        info={
          <>
            <p>
              {level.kind === "global"
                ? "The usage of all environments together, split per environment. "
                : `What ${level.environment.name} used. Global shows all environments together. `}
              Calls on an API key are charged per call. Calls through your Claude subscription cost nothing extra; their
              value is what the same tokens would have cost on the API. Utility calls (titles, commit messages, import)
              are listed apart.
            </p>
            <p>Subscription use never counts towards a budget.</p>
          </>
        }
      />
      <div className="wb-body wb-stack settings-usage">
        {error && <p className="form-error">{error}</p>}
        {worst && (
          <p className={`settings-notice settings-notice-${budgetState(worst)}`}>
            {worst.label}: {money(worst.spent)} of {money(worst.limit)} this month.{" "}
            {budgetState(worst) === "reached"
              ? "Used up: replies through an API key wait until next month."
              : "Almost used up."}
          </p>
        )}
        {report && totals && (
          <>
            <div className="wb-grid-4">
              <div className="wb-kpi">
                <div className="wb-kpi-label">Charged (API)</div>
                <div className="wb-kpi-value">{money(totals.cost)}</div>
                <div className="wb-kpi-detail">
                  {ownLimit !== null && report.month === currentMonthKey() ? `of ${money(ownLimit)} budget` : "on API keys"}
                  {totals.unpriced_calls > 0 && ` · ${totals.unpriced_calls} calls without a price`}
                </div>
              </div>
              <div className="wb-kpi">
                <div className="wb-kpi-label">Subscription value</div>
                <div className="wb-kpi-value">{money(totals.subscription_value)}</div>
                <div className="wb-kpi-detail">what the API would cost</div>
              </div>
              <div className="wb-kpi">
                <div className="wb-kpi-label">Calls</div>
                <div className="wb-kpi-value">{totals.calls}</div>
                <div className="wb-kpi-detail">{utilityCalls} utility</div>
              </div>
              <div className="wb-kpi">
                <div className="wb-kpi-label">Tokens in / out</div>
                <div className="wb-kpi-value">
                  {tokens(inputTokens(totals))} / {tokens(totals.output_tokens)}
                </div>
                <div className="wb-kpi-detail">cache hit {cacheShare}%</div>
              </div>
            </div>

            <div className="settings-usage-split">
              <MonthChart months={report.months} selected={report.month} onSelect={setMonth} />
              <Panel title="Budget" flush aside="USD per month">
                {level.kind === "environment" && environmentSetting && (
                  <TextSetting
                    level={level}
                    setting={environmentSetting}
                    label={level.environment.name}
                    inputMode="decimal"
                    placeholder="No own limit"
                    maxLength={12}
                    ownOnly
                    onSave={(value) => store.save("budget_environment", value)}
                    onReset={() => store.reset("budget_environment")}
                  />
                )}
                {totalSetting &&
                  (level.kind === "global" ? (
                    <TextSetting
                      level={level}
                      setting={totalSetting}
                      label="All environments"
                      inputMode="decimal"
                      placeholder="No limit"
                      maxLength={12}
                      ownOnly
                      onSave={(value) => store.save("budget_total", value)}
                      onReset={() => store.reset("budget_total")}
                    />
                  ) : (
                    <FieldRow label="All environments" hint="Applies when it is reached first">
                      <span>{totalLimit !== null ? money(totalLimit) : "No limit"}</span>
                      <Badge tone={isOwn(totalSetting) ? "level" : "neutral"}>
                        {isOwn(totalSetting) ? "from Global" : "default"}
                      </Badge>
                      <button
                        type="button"
                        className="button button-ghost"
                        onClick={() => navigate("/settings/usage?level=global")}
                      >
                        Change at Global
                      </button>
                    </FieldRow>
                  ))}
                {shownBudgets.length > 0 && (
                  <div className="settings-budgets">
                    {shownBudgets.map((budget) => (
                      <BudgetMeter key={budget.scope} budget={budget} />
                    ))}
                  </div>
                )}
              </Panel>
            </div>

            <BreakdownPanel report={report} level={level} />
          </>
        )}
      </div>
    </>
  );
}
