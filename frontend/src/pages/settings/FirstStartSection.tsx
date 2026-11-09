import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { FieldRow, Panel } from "../../components/workbench/Workbench";
import { WELCOME_PATH, useFirstRun } from "../../firstRun/useFirstRun";
import { type SettingsLevel, SectionHeader } from "./shared";

/** Global only: YAAP's starter Blueprint, and the first start once more. */
export default function FirstStartSection({ level }: { level: SettingsLevel }) {
  const navigate = useNavigate();
  const { restart, error } = useFirstRun({ load: false });
  const [busy, setBusy] = useState(false);

  async function runAgain() {
    setBusy(true);
    const status = await restart();
    setBusy(false);
    if (status) navigate(`${WELCOME_PATH}?again`);
  }

  return (
    <>
      <SectionHeader
        level={level}
        title="First start"
        info={<p>The setup a new installation opens with, and YAAP&apos;s starter Blueprint it offers.</p>}
      />
      <div className="wb-body wb-body-narrow wb-stack">
        <Panel title="Getting started" flush>
          <FieldRow label="YAAP's starter" hint="Standards, skills, commands and agents to start with">
            <button type="button" className="button" onClick={() => navigate("/blueprint?add=starter")}>
              Install YAAP&apos;s starter
            </button>
            <p className="settings-help">
              Opens Blueprint › Add from a package. Run again, it shows what is not installed yet or what changed.
            </p>
          </FieldRow>
          <FieldRow label="First start" hint="Connection, project, backups and the starter">
            <button type="button" className="button" disabled={busy} onClick={() => void runAgain()}>
              Run the first start again
            </button>
            <p className="settings-help">Steps that are already set up show as done; nothing is undone.</p>
            {error && <p className="form-error settings-help">{error}</p>}
          </FieldRow>
        </Panel>
      </div>
    </>
  );
}
