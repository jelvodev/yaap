import { type FormEvent, useState } from "react";

import { type CredentialStatus, api, errorMessage } from "../../api/client";
import { Badge, FieldRow } from "../../components/workbench/Workbench";
import { formatDate } from "./shared";

/** A key or token of one environment: paste, test, replace or remove it. Keys are never shown again. */
export default function CredentialField({
  environmentId,
  credential,
  onChanged,
  note,
}: {
  environmentId: number;
  credential: CredentialStatus;
  onChanged: () => Promise<void>;
  /** Shown above the description, for example when the key is not in use. */
  note?: string;
}) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ ok: boolean; text: string } | null>(null);
  const path = { environment_id: environmentId, kind: credential.kind };

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    const { error } = await api.PUT("/api/v1/environments/{environment_id}/credentials/{kind}", {
      params: { path },
      body: { value: value.trim() },
    });
    setBusy(false);
    if (error) {
      setFeedback({ ok: false, text: errorMessage(error) });
      return;
    }
    setValue("");
    setFeedback({ ok: true, text: "The key works and has been saved." });
    await onChanged();
  }

  async function test() {
    setBusy(true);
    const { data, error } = await api.POST("/api/v1/environments/{environment_id}/credentials/{kind}/test", {
      params: { path },
    });
    setBusy(false);
    setFeedback(data ? { ok: data.ok, text: data.message } : { ok: false, text: errorMessage(error) });
    await onChanged();
  }

  async function remove() {
    if (!window.confirm(`Remove the ${credential.label} from this environment?`)) return;
    setBusy(true);
    const { error } = await api.DELETE("/api/v1/environments/{environment_id}/credentials/{kind}", {
      params: { path },
    });
    setBusy(false);
    setFeedback(error ? { ok: false, text: errorMessage(error) } : null);
    await onChanged();
  }

  return (
    <FieldRow label={credential.label} hint="Stored per environment">
      <form className="settings-inline-form" onSubmit={(event) => void save(event)}>
        <label className="visually-hidden" htmlFor={`credential-${credential.kind}`}>
          {credential.label}
        </label>
        <input
          id={`credential-${credential.kind}`}
          type="password"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder={credential.configured ? "Paste a new key to replace it" : "Paste the key"}
          autoComplete="off"
        />
        <button type="submit" className="button button-primary" disabled={busy || value.trim().length < 8}>
          {credential.configured ? "Replace" : "Test and save"}
        </button>
        {credential.configured && (
          <>
            <button type="button" className="button" onClick={() => void test()} disabled={busy}>
              Test
            </button>
            <button type="button" className="button button-danger" onClick={() => void remove()} disabled={busy}>
              Remove
            </button>
          </>
        )}
      </form>
      {credential.configured ? (
        <Badge tone="ok">configured ••••{credential.last_four}</Badge>
      ) : (
        <Badge tone="warn">not set</Badge>
      )}
      {note && <p className="settings-help settings-note">{note}</p>}
      <p className="settings-help">{credential.description}</p>
      {credential.configured && (
        <p className="settings-help">
          Set {formatDate(credential.updated_at)}
          {credential.last_tested_at &&
            ` · last test ${formatDate(credential.last_tested_at)}: ${credential.last_test_result}`}
        </p>
      )}
      <p className="settings-help">A new key is tested before it is saved. Saved keys are encrypted and never shown again.</p>
      {feedback && <p className={`settings-help ${feedback.ok ? "form-ok" : "form-error"}`}>{feedback.text}</p>}
    </FieldRow>
  );
}
