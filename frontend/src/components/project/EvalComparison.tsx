import { type FormEvent, useState } from "react";

import { money } from "./EvalRunDetail";
import { type EvalComparison, type EvalRun, type EvalVariant, useEvalComparison } from "./useEvals";

type Side = EvalComparison["cases"][number]["a"];

function runLabel(run: EvalRun): string {
  return `#${run.id} · ${run.variant?.label ?? "normal setup"}${run.repeats > 1 ? ` · ×${run.repeats}` : ""}`;
}

function rate(value: number | null | undefined): string {
  return value === null || value === undefined ? "–" : `${Math.round(value * 100)}%`;
}

function signed(value: number | null | undefined, format: (value: number) => string): string {
  if (value === null || value === undefined) return "–";
  return value > 0 ? `+${format(value)}` : format(value);
}

function SideCell({ side }: { side: Side }) {
  return (
    <td>
      <span className={`pp-eval-${side.verdict === "mixed" ? "error" : side.verdict}`}>{side.verdict}</span>
      <span className="faint small">
        {" "}
        {side.passed}/{side.plays} · {money(side.median_cost)}
        {side.errors > 0 && ` · ${side.errors} ${side.errors === 1 ? "error" : "errors"}`}
        {side.stopped > 0 && ` · ${side.stopped} stopped`}
      </span>
    </td>
  );
}

/** The variant to compare with: another preset, agent, or a Blueprint layer at a git ref. */
function VariantForm({ onStart }: { onStart: (variant: EvalVariant, repeats: number) => Promise<void> }) {
  const [preset, setPreset] = useState("");
  const [agent, setAgent] = useState("");
  const [layer, setLayer] = useState<"" | "global" | "environment" | "project">("");
  const [ref, setRef] = useState("");
  const [repeats, setRepeats] = useState(3);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    await onStart(
      { preset: preset || null, agent: agent || null, layer: layer || null, ref: layer ? ref || null : null },
      repeats,
    );
    setBusy(false);
  }

  return (
    <form className="pp-task-editor" onSubmit={(event) => void submit(event)}>
      <div className="pp-row">
        <label>
          Preset
          <input value={preset} maxLength={40} placeholder="as usual" onChange={(event) => setPreset(event.target.value)} />
        </label>
        <label>
          Agent
          <input value={agent} maxLength={80} placeholder="as usual" onChange={(event) => setAgent(event.target.value)} />
        </label>
      </div>
      <div className="pp-row">
        <label>
          Blueprint layer
          <select value={layer} onChange={(event) => setLayer(event.target.value as typeof layer)}>
            <option value="">as usual</option>
            <option value="project">Project</option>
            <option value="environment">Environment</option>
            <option value="global">Global</option>
          </select>
        </label>
        <label>
          at git ref
          <input value={ref} disabled={!layer} placeholder="a branch" required={Boolean(layer)} onChange={(event) => setRef(event.target.value)} />
        </label>
        <label>
          Plays per case
          <input type="number" min={1} max={10} value={repeats} onChange={(event) => setRepeats(Number(event.target.value) || 1)} />
        </label>
      </div>
      <div className="pp-row">
        <button type="submit" className="button button-primary" disabled={busy || (!preset && !agent && !layer)}>
          Compare with the normal setup
        </button>
      </div>
    </form>
  );
}

/** Per case the verdict on each side, the differences B minus A, and the pass rates. */
export function ComparisonTable({ comparison }: { comparison: EvalComparison }) {
  return (
    <table className="pp-eval-compare small">
      <thead>
        <tr>
          <th>Case</th>
          <th>A</th>
          <th>B</th>
          <th>Cost B−A</th>
          <th>Tokens B−A</th>
          <th>Steps B−A</th>
        </tr>
      </thead>
      <tbody>
        {comparison.cases.map((row) => (
          <tr key={row.case}>
            <td className="mono">{row.case}</td>
            <SideCell side={row.a} />
            <SideCell side={row.b} />
            <td>{signed(row.cost_difference, (value) => money(value))}</td>
            <td>{signed(row.tokens_difference, (value) => Math.round(value).toLocaleString())}</td>
            <td>{signed(row.steps_difference, (value) => String(value))}</td>
          </tr>
        ))}
        <tr>
          <td>Pass rate</td>
          <td>{rate(comparison.pass_rate_a)}</td>
          <td>{rate(comparison.pass_rate_b)}</td>
          <td colSpan={3} />
        </tr>
      </tbody>
    </table>
  );
}

/** Two runs side by side: per case the verdict, pass rate over the plays, median cost and B minus A. */
export default function EvalComparisonView({
  projectId,
  runs,
  onStartComparison,
}: {
  projectId: number;
  runs: EvalRun[];
  onStartComparison: (variant: EvalVariant, repeats: number) => Promise<EvalRun[] | null>;
}) {
  const [a, setA] = useState<number | null>(null);
  const [b, setB] = useState<number | null>(null);
  const comparison = useEvalComparison(projectId, a, b, runs);

  async function start(variant: EvalVariant, repeats: number) {
    const started = await onStartComparison(variant, repeats);
    if (started && started.length === 2) {
      setA(started[0].id);
      setB(started[1].id);
    }
  }

  return (
    <div className="pp-tasks">
      <VariantForm onStart={start} />
      <div className="pp-row">
        {[
          { label: "A", value: a, set: setA },
          { label: "B", value: b, set: setB },
        ].map((side) => (
          <label key={side.label} className="pp-grow">
            Run {side.label}
            <select value={side.value ?? ""} onChange={(event) => side.set(event.target.value ? Number(event.target.value) : null)}>
              <option value="">Choose a run</option>
              {runs.map((run) => (
                <option key={run.id} value={run.id}>
                  {runLabel(run)}
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>
      {comparison && <ComparisonTable comparison={comparison} />}
    </div>
  );
}
