import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { type Environment, api, errorMessage } from "../../api/client";
import { formatSize } from "../../api/attachments";
import type { components } from "../../api/schema";
import { Badge, EmptyState, FieldRow, Panel } from "../../components/workbench/Workbench";
import { useEnvironment } from "../../environment/EnvironmentProvider";
import { DestinationsPanel, RunsPanel, SchedulePanel } from "./BackupPanels";
import EnvironmentNameField from "./EnvironmentNameField";
import RestorePanel from "./RestorePanel";
import { type LevelSettings, type SettingsLevel, SectionHeader, TextSetting } from "./shared";

type Backup = components["schemas"]["BackupOut"];

/** Global only: who you are. */
export function ProfileSection({ level, store }: { level: SettingsLevel; store: LevelSettings }) {
  const displayName = store.find("display_name");
  const timezone = store.find("timezone");
  return (
    <>
      <SectionHeader
        level={level}
        title="Profile"
        info={<p>Your name, shown at the global level and in greetings. It is set once for every environment.</p>}
      />
      <div className="wb-body wb-body-narrow wb-stack">
        <Panel title="Profile" flush>
          {displayName && (
            <TextSetting
              level={level}
              setting={displayName}
              placeholder="Your name"
              maxLength={60}
              ownOnly
              onSave={(value) => store.save("display_name", value)}
              onReset={() => store.reset("display_name")}
            />
          )}
          {timezone && (
            <TextSetting
              level={level}
              setting={timezone}
              placeholder="Europe/Amsterdam"
              maxLength={60}
              ownOnly
              onSave={(value) => store.save("timezone", value)}
              onReset={() => store.reset("timezone")}
            />
          )}
        </Panel>
      </div>
    </>
  );
}

/** Global only: the environments, and a form for a new one. */
export function EnvironmentsSection({ level }: { level: SettingsLevel }) {
  const navigate = useNavigate();
  const { environments, active, reloadEnvironments } = useEnvironment();
  const [newName, setNewName] = useState("");
  const [newColour, setNewColour] = useState("#10B981");
  const [message, setMessage] = useState<string | null>(null);

  async function create(event: FormEvent) {
    event.preventDefault();
    const { error } = await api.POST("/api/v1/environments", { body: { name: newName.trim(), colour: newColour } });
    if (error) {
      setMessage(errorMessage(error));
      return;
    }
    setMessage(null);
    setNewName("");
    await reloadEnvironments();
  }

  return (
    <>
      <SectionHeader
        level={level}
        title="Environments"
        badges={<Badge>{environments.length}</Badge>}
        info={
          <p>
            {environments.length} environment{environments.length === 1 ? "" : "s"}. Keys, projects and their own
            overrides are set inside each environment.
          </p>
        }
      />
      <div className="wb-body wb-body-narrow wb-stack">
        <Panel title="Environments" flush>
          {environments.length === 0 ? (
            <EmptyState>No environments yet.</EmptyState>
          ) : (
            <div className="wb-table-scroll settings-scroll">
              <table className="wb-table">
                <tbody>
                  {environments.map((environment) => (
                    <tr key={environment.id}>
                      <td>
                        <span className="settings-repository-name">
                          <span className="dot" style={{ background: environment.colour }} />
                          {environment.name}
                        </span>
                      </td>
                      <td>{active?.id === environment.id && <Badge tone="accent">active</Badge>}</td>
                      <td className="wb-num">
                        <button
                          type="button"
                          className="button button-ghost"
                          onClick={() => navigate(`/settings/connections?environment=${environment.id}`)}
                        >
                          Open settings
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
        <Panel title="New environment" flush>
          <FieldRow label="Name and colour">
            <form className="settings-inline-form" onSubmit={(event) => void create(event)}>
              <label className="visually-hidden" htmlFor="new-environment-colour">
                Colour
              </label>
              <input
                id="new-environment-colour"
                type="color"
                className="settings-colour"
                value={newColour}
                onChange={(event) => setNewColour(event.target.value)}
              />
              <label className="visually-hidden" htmlFor="new-environment-name">
                Name
              </label>
              <input
                id="new-environment-name"
                value={newName}
                onChange={(event) => setNewName(event.target.value)}
                placeholder="Name, for example Side projects"
                maxLength={80}
              />
              <button type="submit" className="button button-primary" disabled={!newName.trim()}>
                Add
              </button>
            </form>
            {message && <p className="form-error settings-help">{message}</p>}
          </FieldRow>
        </Panel>
      </div>
    </>
  );
}

/** Global only: backups of the data folder; make one now, download, upload and restore. */
export function BackupSection({ level }: { level: SettingsLevel }) {
  const [backups, setBackups] = useState<Backup[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ error: boolean; text: string } | null>(null);
  const [restoring, setRestoring] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const upload = useRef<HTMLInputElement>(null);

  async function uploadBackup(file: File | undefined) {
    if (!file) return;
    setUploading(true);
    setMessage(null);
    const form = new FormData();
    form.append("file", file, file.name);
    const response = await fetch("/api/v1/backups/upload", { method: "POST", body: form });
    const body = await response.json().catch(() => null);
    setUploading(false);
    if (upload.current) upload.current.value = "";
    if (!response.ok) {
      setMessage({ error: true, text: errorMessage(body, `The upload failed (HTTP ${response.status}).`) });
      return;
    }
    setMessage({ error: false, text: `Backup ${(body as Backup).name} added.` });
    void load();
  }

  const load = useCallback(async () => {
    const { data } = await api.GET("/api/v1/backups");
    setBackups(data ?? []);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function create() {
    setBusy(true);
    setMessage(null);
    const { data, error } = await api.POST("/api/v1/backups");
    setBusy(false);
    if (error || !data) {
      setMessage({ error: true, text: errorMessage(error, "The backup failed.") });
      return;
    }
    setMessage({ error: false, text: `Backup ${data.name} made (${formatSize(data.size)}).` });
    setRefresh((count) => count + 1);
    void load();
  }

  return (
    <>
      <SectionHeader
        level={level}
        title="Backup"
        actions={
          <>
            <button type="button" className="button" disabled={uploading} onClick={() => upload.current?.click()}>
              {uploading ? "Uploading…" : "Upload backup"}
            </button>
            <input
              ref={upload}
              type="file"
              accept=".zip,application/zip"
              hidden
              aria-label="Backup to upload"
              onChange={(event) => void uploadBackup(event.target.files?.[0])}
            />
            <button type="button" className="button button-primary" disabled={busy} onClick={() => void create()}>
              {busy ? "Making backup…" : "Make backup now"}
            </button>
          </>
        }
        info={
          <p>
            One zip in <span className="mono">data/backups</span> with the database, the Blueprint with its history,
            the attachments and the project folders (without node_modules and similar folders), made on a schedule
            and copied to the destinations. Not included: the master key, which you keep yourself, and the Claude
            Code sign-in. Restore replaces YAAP's data with a backup, after making a fresh backup of the current
            state.
          </p>
        }
      />
      <div className="wb-body wb-body-narrow wb-stack">
        {message && <p className={message.error ? "form-error" : "form-ok"}>{message.text}</p>}
        {restoring && (
          <Panel title="Restore">
            <RestorePanel key={restoring} name={restoring} onClose={() => setRestoring(null)} />
          </Panel>
        )}
        <p className="settings-help">
          Not included: the master key, which you keep yourself, and the Claude Code sign-in.
        </p>
        <SchedulePanel onChanged={() => setRefresh((count) => count + 1)} />
        <DestinationsPanel
          refresh={refresh}
          onFetched={(name) => {
            void load();
            setRestoring(name);
          }}
        />
        <Panel title="Backups" flush aside="data/backups">
          {backups === null ? (
            <EmptyState>Loading…</EmptyState>
          ) : backups.length === 0 ? (
            <EmptyState>No backups yet.</EmptyState>
          ) : (
            <div className="wb-table-scroll settings-scroll">
              <table className="wb-table">
                <thead>
                  <tr>
                    <th scope="col">File</th>
                    <th scope="col">Made</th>
                    <th scope="col" className="wb-num">
                      Size
                    </th>
                    <th scope="col">
                      <span className="visually-hidden">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {backups.map((backup) => (
                    <tr key={backup.name}>
                      <td>
                        <a href={`/api/v1/backups/${backup.name}`} download className="mono">
                          {backup.name}
                        </a>
                      </td>
                      <td>{new Date(backup.created_at).toLocaleString()}</td>
                      <td className="wb-num">{formatSize(backup.size)}</td>
                      <td>
                        <button type="button" className="link-button small" onClick={() => setRestoring(backup.name)}>
                          Restore…
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
        <RunsPanel refresh={refresh} />
      </div>
    </>
  );
}

/** Environment only: its name and colour. */
export function AppearanceSection({ level, environment }: { level: SettingsLevel; environment: Environment }) {
  return (
    <>
      <SectionHeader
        level={level}
        title="Appearance"
        badges={<Badge tone="level" colour={environment.colour}>{environment.name}</Badge>}
        info={<p>The name and colour of {environment.name}, shown in the header, the level pickers and badges.</p>}
      />
      <div className="wb-body wb-body-narrow wb-stack">
        <Panel title="Name and colour" flush>
          <EnvironmentNameField environment={environment} />
        </Panel>
      </div>
    </>
  );
}
