import { type FormEvent, useState } from "react";

import type { ReplayVariant } from "./useReplays";

type Layer = "" | "global" | "environment" | "project";

interface ReplayFormProps {
  onStart: (variant: ReplayVariant) => Promise<boolean>;
  onCancel: () => void;
}

/** What the replay changes: another preset, another model, or a Blueprint layer at a git ref; all empty replays the
 * call as it was set up. */
export default function ReplayForm({ onStart, onCancel }: ReplayFormProps) {
  const [preset, setPreset] = useState("");
  const [model, setModel] = useState("");
  const [layer, setLayer] = useState<Layer>("");
  const [ref, setRef] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    const started = await onStart({
      preset: preset || null,
      model: model || null,
      layer: layer || null,
      ref: layer ? ref || null : null,
    });
    setBusy(false);
    if (started) onCancel();
  }

  return (
    <form className="trace-replay-form" onSubmit={(event) => void submit(event)}>
      <p className="faint small">
        A new run starts with what this call saw, in a copy of the project's files of that moment. It runs unattended:
        calls that would ask are refused.
      </p>
      <div className="trace-replay-row">
        <label>
          Preset
          <input
            value={preset}
            maxLength={40}
            placeholder="as it was"
            onChange={(event) => setPreset(event.target.value)}
          />
        </label>
        <label>
          Model
          <input
            value={model}
            maxLength={80}
            placeholder="the preset's"
            onChange={(event) => setModel(event.target.value)}
          />
        </label>
      </div>
      <div className="trace-replay-row">
        <label>
          Blueprint layer
          <select value={layer} onChange={(event) => setLayer(event.target.value as Layer)}>
            <option value="">as it is</option>
            <option value="project">Project</option>
            <option value="environment">Environment</option>
            <option value="global">Global</option>
          </select>
        </label>
        <label>
          at git ref
          <input
            value={ref}
            disabled={!layer}
            maxLength={200}
            placeholder="a branch"
            required={Boolean(layer)}
            onChange={(event) => setRef(event.target.value)}
          />
        </label>
      </div>
      <div className="trace-replay-row">
        <button type="submit" className="button button-primary" disabled={busy}>
          Replay
        </button>
        <button type="button" className="button button-ghost" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
