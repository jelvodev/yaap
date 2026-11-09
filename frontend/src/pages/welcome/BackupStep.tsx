import { useState } from "react";

import { Panel } from "../../components/workbench/Workbench";
import { DestinationsPanel, SchedulePanel } from "../settings/BackupPanels";

/** Where backups go and how often; the rest of Backup (restore, runs) stays in Settings › System. */
export default function BackupStep({ onChanged }: { onChanged: () => void }) {
  const [refresh, setRefresh] = useState(0);
  return (
    <>
      <Panel title="Backups">
        <p>
          YAAP backs up its data folder: the database, the Blueprint and your projects. Backups stay in data/backups;
          a destination copies them off that disk, to a folder or a network share.
        </p>
        <p className="muted small">
          Not included: the master key, which you keep yourself, and the Claude Code sign-in. Restore is in Settings ›
          System › Backup.
        </p>
      </Panel>
      <SchedulePanel
        onChanged={() => {
          setRefresh((count) => count + 1);
          onChanged();
        }}
      />
      <DestinationsPanel
        refresh={refresh}
        // Copying a backup back is the first step of a restore, which belongs to Settings; the panel reports it.
        onFetched={() => onChanged()}
      />
    </>
  );
}
