import { useState } from "react";

import DiffView from "../DiffView";
import { ComparisonTable } from "./EvalComparison";
import { useEvalComparison } from "./useEvals";
import { type Learning, type LearningProposal, type LearningProposals, type LearningRule, useLearning } from "./useLearning";

const KIND_LABELS: Record<LearningProposal["kind"], string> = {
  rule: "Rule",
  memory: "Memory line",
  skill_step: "Skill step",
  rule_removal: "Remove rule",
};

const SIGNAL_LABELS: Record<LearningProposal["signals"][number]["signal"], string> = {
  declined: "Declined",
  test_failed_twice: "Test failed twice",
  known_search: "Searched for what the Blueprint holds",
};

const EFFECT_LABELS: Record<NonNullable<LearningProposal["measurement"]["effect"]>, string> = {
  better: "the pass rate rose with it",
  worse: "the pass rate fell with it",
  same: "no difference measured",
};

function rate(value: number | null): string {
  return value === null ? "–" : `${Math.round(value * 100)}%`;
}

function ruleKey(rule: LearningRule): string {
  return `${rule.level}|${rule.path}|${rule.rule}`;
}

/** Where the proposal stands with its measurement, and the comparison once both runs are done. */
function MeasurementView({ projectId, proposal }: { projectId: number; proposal: LearningProposal }) {
  const { measurement } = proposal;
  const measured = measurement.status === "measured";
  const comparison = useEvalComparison(
    projectId,
    measured ? measurement.baseline_run_id : null,
    measured ? measurement.variant_run_id : null,
    null,
  );
  const text: Record<LearningProposal["measurement"]["status"], string> = {
    ready: "Not measured yet: Measure plays the project's eval cases with and without it.",
    measuring: `Measuring: runs #${measurement.baseline_run_id} and #${measurement.variant_run_id} in the Evals tab.`,
    measured: `Measured: pass rate ${rate(measurement.pass_rate_without)} without, ${rate(measurement.pass_rate_with)} with it${
      measurement.effect ? `; ${EFFECT_LABELS[measurement.effect]}` : ""
    }.`,
    not_measured: `Not measured. ${measurement.note ?? ""}`,
    not_needed: "A memory line is written when you accept it; it is not measured.",
  };
  return (
    <>
      <span className={measurement.status === "not_measured" ? "muted small" : "small"}>{text[measurement.status]}</span>
      {comparison && <ComparisonTable comparison={comparison} />}
    </>
  );
}

interface ProposalRowProps {
  projectId: number;
  proposal: LearningProposal;
  learning: Learning;
}

function ProposalRow({ projectId, proposal, learning }: ProposalRowProps) {
  const [showDiff, setShowDiff] = useState(false);
  const busy = learning.busy !== null;
  const status = proposal.measurement.status;
  const canMeasure = proposal.branch !== null && status !== "measuring" && status !== "not_needed";
  return (
    <li className="pp-task">
      <div className="pp-task-head">
        <strong className="small">{KIND_LABELS[proposal.kind]}</strong>
        <span className="faint small">
          {proposal.level} · <span className="mono">{proposal.target}</span>
          {proposal.source === "fallback" && " · without the utility model"}
        </span>
      </div>
      <span className="small">{proposal.text}</span>
      {proposal.rationale && <span className="muted small">{proposal.rationale}</span>}
      {proposal.signals.map((signal) => (
        <span key={signal.step_id} className="faint small">
          {SIGNAL_LABELS[signal.signal]}: {signal.tool} ({signal.summary}){signal.note ? ` · ${signal.note}` : ""}
        </span>
      ))}
      <MeasurementView projectId={projectId} proposal={proposal} />
      {showDiff && <DiffView diff={proposal.diff} />}
      <div className="pp-row">
        <button type="button" className="button button-primary" disabled={busy} onClick={() => void learning.accept(proposal)}>
          Accept
        </button>
        <button type="button" className="button" disabled={busy} onClick={() => void learning.decline(proposal)}>
          Decline
        </button>
        {canMeasure && (
          <button type="button" className="button button-ghost" disabled={busy} onClick={() => void learning.measure(proposal)}>
            {proposal.measurement.status === "measured" ? "Measure again" : "Measure"}
          </button>
        )}
        <button type="button" className="button button-ghost" aria-expanded={showDiff} onClick={() => setShowDiff((value) => !value)}>
          {showDiff ? "Hide diff" : "Diff"}
        </button>
      </div>
    </li>
  );
}

/** Measure one rule without it, and look for rules that contradict each other across levels. */
function RuleChecks({ learning }: { learning: Learning }) {
  const [chosen, setChosen] = useState("");
  const rule = learning.rules.find((item) => ruleKey(item) === chosen);
  return (
    <>
      <h3 className="small">Check the rules</h3>
      <div className="pp-row">
        <label className="pp-grow">
          Rule
          <select value={chosen} onChange={(event) => setChosen(event.target.value)}>
            <option value="">Choose a rule</option>
            {learning.rules.map((item) => (
              <option key={ruleKey(item)} value={ruleKey(item)}>
                [{item.level}] {item.rule}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="button"
          disabled={!rule || learning.busy !== null}
          onClick={() => rule && void learning.checkRule(rule)}
        >
          Measure without it
        </button>
      </div>
      <div className="pp-row">
        <button
          type="button"
          className="button"
          disabled={learning.busy !== null || learning.rules.length < 2}
          onClick={() => void learning.findContradictions()}
        >
          {learning.busy === "contradictions" ? "Checking…" : "Find contradictions"}
        </button>
      </div>
      {learning.report && !learning.report.available && (
        <p className="muted small">No utility model is available to check the rules.</p>
      )}
      {learning.report?.available && learning.report.contradictions.length === 0 && (
        <p className="faint small">No contradictions found.</p>
      )}
      <ul className="pp-task-list">
        {learning.report?.contradictions.map((item) => (
          <li key={item.explanation} className="pp-task">
            <span className="small">{item.explanation}</span>
            {item.rules.map((rule) => (
              <span key={ruleKey(rule)} className="faint small">
                [{rule.level}] <span className="mono">{rule.path}</span>: {rule.rule}
              </span>
            ))}
          </li>
        ))}
      </ul>
    </>
  );
}

/** What the work suggests for the Blueprint and the memory: each proposal with its reasons, diff and
 * measurement, to accept or decline. Nothing is applied without the user. */
export default function LearningTab({ projectId, proposals }: { projectId: number; proposals: LearningProposals }) {
  const learning = useLearning(projectId, proposals);
  if (learning.proposals === null) return <p className="faint small pp-pad">Loading…</p>;
  return (
    <div className="pp-pad pp-tasks">
      <p className="faint small">
        Proposals come from what went wrong in the work: declined actions, tests that failed twice, searches for what the
        Blueprint already says. A Blueprint proposal waits on a branch of its layer and can be measured with the project's
        eval cases before you accept it.
      </p>
      {learning.error && <p className="form-error small">{learning.error}</p>}
      {learning.notice && <p className="muted small">{learning.notice}</p>}
      {learning.proposals.length === 0 && <p className="faint small">No proposals waiting.</p>}
      <ul className="pp-task-list">
        {learning.proposals.map((proposal) => (
          <ProposalRow key={proposal.id} projectId={projectId} proposal={proposal} learning={learning} />
        ))}
      </ul>
      <RuleChecks learning={learning} />
    </div>
  );
}
