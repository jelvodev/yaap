import { useEffect, useState } from "react";

import { Markdown } from "../../components/message/Markdown";
import { Badge, FieldRow, Panel } from "../../components/workbench/Workbench";
import { markSeen } from "../../updates/seenVersion";
import { useRunningBuild } from "../../updates/useRunningBuild";
import { useUpdateStatus } from "../../updates/useUpdateStatus";
import { type LevelSettings, type SettingsLevel, SectionHeader, TextSetting } from "./shared";

const COMMANDS = [
  { system: "Windows", command: "powershell -ExecutionPolicy Bypass -File scripts\\update.ps1" },
  { system: "macOS and Linux", command: "./scripts/update.sh" },
] as const;

function when(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString() : "never";
}

function CopyCommand({ system, command }: { system: string; command: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard access can be refused; the command stays selectable.
    }
  }
  return (
    <FieldRow label={system} hint="Run it in the folder YAAP was installed in">
      <div className="settings-inline-form">
        <code className="mono">{command}</code>
        <button type="button" className="button" onClick={() => void copy()}>
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
    </FieldRow>
  );
}

/** Global only: the running version, the newest release with its notes, and how often YAAP looks. */
export default function UpdatesSection({ level, store }: { level: SettingsLevel; store: LevelSettings }) {
  const build = useRunningBuild();
  const { status, checking, problem, checkNow } = useUpdateStatus();
  const schedule = store.find("update_check_schedule");
  const source = store.find("update_source");
  const newest = status?.newest_version ?? null;

  // Opening the section for a version clears the dot on Settings.
  useEffect(() => {
    if (newest) markSeen(newest);
  }, [newest]);

  const link = status?.release_url?.startsWith("https://github.com/") ? status.release_url : null;
  return (
    <>
      <SectionHeader
        level={level}
        title="Updates"
        info={
          <p>
            YAAP asks GitHub for the newest release a few seconds after it starts and once a day, and tells you when a
            newer one exists. It sends nothing about you. Updating is a command you run yourself.
          </p>
        }
      />
      <div className="wb-body wb-body-narrow wb-stack">
        <Panel title="This version" flush>
          <FieldRow label="Version">
            <span className="mono">{build?.version ?? status?.current_version ?? "…"}</span>
          </FieldRow>
          <FieldRow label="Commit">
            {build && build.commit ? (
              <span className="mono">{build.commit.slice(0, 12)}</span>
            ) : (
              <Badge>development build</Badge>
            )}
          </FieldRow>
        </Panel>
        <Panel
          title="Newest version"
          flush
          aside={
            status?.update_available ? (
              <Badge tone="warn">Update available</Badge>
            ) : newest ? (
              <Badge tone="ok">Up to date</Badge>
            ) : null
          }
        >
          <FieldRow label="Newest">
            {newest ? (
              <>
                <span className="mono">{newest}</span>
                {link && (
                  <>
                    {" "}
                    <a href={link} target="_blank" rel="noopener noreferrer">
                      On GitHub
                    </a>
                  </>
                )}
              </>
            ) : (
              <span className="muted">Not known yet</span>
            )}
          </FieldRow>
          {status?.notes ? (
            <FieldRow label="What changed" hint="From the project's changelog; Action needed lists what you must do">
              <div className="update-notes">
                <Markdown text={status.notes} />
              </div>
            </FieldRow>
          ) : null}
          <FieldRow label="Last check" hint="Also when it failed; the result above is then the one before">
            <div className="settings-inline-form">
              <span>{when(status?.last_checked_at)}</span>
              <button type="button" className="button" disabled={checking} onClick={() => void checkNow()}>
                {checking ? "Checking…" : "Check now"}
              </button>
            </div>
            {status?.error && <p className="form-error settings-help">{status.error}</p>}
            {problem && <p className="form-error settings-help">{problem}</p>}
          </FieldRow>
        </Panel>
        <Panel title="Update" flush>
          {COMMANDS.map((item) => (
            <CopyCommand key={item.system} {...item} />
          ))}
        </Panel>
        <Panel title="Checking" flush>
          {schedule && (
            <TextSetting
              level={level}
              setting={schedule}
              placeholder="0 9 * * *  or  off"
              maxLength={100}
              onSave={(value) => store.save(schedule.key, value.trim())}
              onReset={() => store.reset(schedule.key)}
            />
          )}
          {source && (
            <TextSetting
              level={level}
              setting={source}
              placeholder="jelvodev/yaap"
              maxLength={100}
              onSave={(value) => store.save(source.key, value.trim())}
              onReset={() => store.reset(source.key)}
            />
          )}
        </Panel>
      </div>
    </>
  );
}
