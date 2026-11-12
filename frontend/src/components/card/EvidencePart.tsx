import { type ReactNode, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import type { components } from "../../api/schema";
import { checksOfStep, diffStatText, findCheck, isUnmet, outcomeLabel, statusView, stepNumber } from "./evidenceLogic";

type Evidence = components["schemas"]["EvidenceOut"];
type Criterion = components["schemas"]["CriterionEvidenceOut"];
type CheckRun = components["schemas"]["CheckRunOut"];

const HOW_LABELS = { command: "by command", file: "by file", reviewer: "by the reviewer", none: "not checked" } as const;

interface Opened {
  title: string;
  checks: CheckRun[];
}

/** The command and output of checks, as plain text in a dialog. */
function SourceDialog({ opened, onClose }: { opened: Opened; onClose: () => void }) {
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="palette-backdrop" onMouseDown={onClose}>
      <div
        className="palette import-dialog evidence-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={opened.title}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <h2>{opened.title}</h2>
        {opened.checks.length === 0 && <p className="small">No check output was recorded for this.</p>}
        {opened.checks.map((check, index) => (
          <div key={`${check.ref}-${index}`} className="evidence-check">
            <code>{check.command}</code>
            <span className="card-muted"> {outcomeLabel(check.outcome)}</span>
            <pre>{check.output || "(no output)"}</pre>
          </div>
        ))}
        <div className="import-row import-actions">
          <button type="button" className="button" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

function CriterionSource({
  criterion,
  checks,
  onOpen,
}: {
  criterion: Criterion;
  checks: CheckRun[];
  onOpen: (opened: Opened) => void;
}) {
  const source = criterion.source;
  if (!source || source.kind === "screenshot") return null;
  if (source.kind === "finding") {
    return (
      <span className="evidence-source">
        <code>{source.ref}</code>
      </span>
    );
  }
  if (source.kind === "run_step") {
    const step = stepNumber(source.ref);
    const title = step === null ? "Step of the plan" : `Step ${step} of the plan`;
    return (
      <span className="evidence-source">
        <span className="card-muted">{title} </span>
        <button
          type="button"
          className="link-button"
          onClick={() => onOpen({ title, checks: checksOfStep(checks, source.ref) })}
        >
          Show source
        </button>
      </span>
    );
  }
  const found = findCheck(checks, source.ref);
  return (
    <span className="evidence-source">
      <button
        type="button"
        className="link-button"
        onClick={() => onOpen({ title: "Check output", checks: found ? [found] : [] })}
      >
        Show source
      </button>
    </span>
  );
}

/** What the reviewer relied on for each criterion, the checks that ran, the size of the change and the trace. */
export default function EvidencePart({
  evidence,
  branch,
  busy,
  onSendBack,
}: {
  evidence: Evidence;
  /** The branch link the review part shows next to the diff stat. */
  branch: ReactNode;
  busy: boolean;
  onSendBack: (criterion: number) => void;
}) {
  const [opened, setOpened] = useState<Opened | null>(null);
  const { criteria, checks_run: checks, diff_stat: stat, trace_ref: trace, notes } = evidence;
  return (
    <div className="card-evidence">
      {criteria.length > 0 && (
        <>
          <h4 className="card-section-label">Acceptance criteria</h4>
          <ul className="evidence-criteria" aria-label="Acceptance criteria">
            {criteria.map((criterion) => {
              const view = statusView(criterion.status);
              return (
                <li key={criterion.number} data-tone={view.tone}>
                  <span className="evidence-mark" aria-hidden="true">
                    {view.mark}
                  </span>
                  <div className="evidence-body">
                    <span>{criterion.text}</span>
                    <span className="card-muted evidence-how">
                      {view.label}, {HOW_LABELS[criterion.how]}
                    </span>
                    {criterion.note && <p className="card-muted evidence-note">{criterion.note}</p>}
                    <CriterionSource criterion={criterion} checks={checks} onOpen={setOpened} />
                    {isUnmet(criterion.status) && (
                      <button
                        type="button"
                        className="link-button"
                        disabled={busy}
                        onClick={() => onSendBack(criterion.number)}
                      >
                        Send back with the finding
                      </button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      )}
      {checks.length > 0 && (
        <div className="card-checks">
          <h4>Checks that ran</h4>
          <ul>
            {checks.map((check, index) => (
              <li key={`${check.ref}-${index}`} data-outcome={check.outcome}>
                <code>{check.command}</code>
                <span>
                  {check.step}, {outcomeLabel(check.outcome)}
                </span>
                <button
                  type="button"
                  className="link-button"
                  onClick={() => setOpened({ title: "Check output", checks: [check] })}
                >
                  Show output
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      <p className="card-muted">
        {diffStatText(stat)}. {branch}
        {trace && (
          <>
            {" "}
            <Link to={`/chat/${trace.conversation_id}`}>Show in trace</Link>
          </>
        )}
      </p>
      {notes.map((note, index) => (
        <p key={index} className="card-muted">
          {note}
        </p>
      ))}
      {opened && <SourceDialog opened={opened} onClose={() => setOpened(null)} />}
    </div>
  );
}
