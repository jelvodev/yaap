import { type FormEvent, useEffect, useState } from "react";

import { type Environment, api, errorMessage } from "../../api/client";
import { FieldRow } from "../../components/workbench/Workbench";
import { useEnvironment } from "../../environment/EnvironmentProvider";

/** The name and colour of one environment, saved together. Used by Settings › Appearance and the first start. */
export default function EnvironmentNameField({
  environment,
  label = "Environment",
  hint = "Only for this environment",
}: {
  environment: Environment;
  label?: string;
  hint?: string;
}) {
  const { reloadEnvironments } = useEnvironment();
  const [name, setName] = useState(environment.name);
  const [colour, setColour] = useState(environment.colour);
  const [message, setMessage] = useState<{ error: boolean; text: string } | null>(null);
  const id = `environment-${environment.id}`;

  useEffect(() => {
    setName(environment.name);
    setColour(environment.colour);
  }, [environment.name, environment.colour]);

  async function save(event: FormEvent) {
    event.preventDefault();
    const { error } = await api.PATCH("/api/v1/environments/{environment_id}", {
      params: { path: { environment_id: environment.id } },
      body: { name: name.trim(), colour },
    });
    setMessage(error ? { error: true, text: errorMessage(error) } : { error: false, text: "Saved." });
    if (!error) await reloadEnvironments();
  }

  const changed = name.trim() !== environment.name || colour.toUpperCase() !== environment.colour.toUpperCase();

  return (
    <FieldRow label={label} hint={hint}>
      <form className="settings-inline-form" onSubmit={(event) => void save(event)}>
        <label className="visually-hidden" htmlFor={`${id}-colour`}>
          Colour
        </label>
        <input
          id={`${id}-colour`}
          type="color"
          className="settings-colour"
          value={colour}
          onChange={(event) => setColour(event.target.value)}
        />
        <label className="visually-hidden" htmlFor={`${id}-name`}>
          Name
        </label>
        <input id={`${id}-name`} value={name} onChange={(event) => setName(event.target.value)} maxLength={80} />
        <button type="submit" className="button button-primary" disabled={!changed || !name.trim()}>
          Save
        </button>
      </form>
      {message && <p className={`settings-help ${message.error ? "form-error" : "form-ok"}`}>{message.text}</p>}
    </FieldRow>
  );
}
