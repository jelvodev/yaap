import { useEffect, useState } from "react";

import type { Setting } from "../../api/client";
import { FieldRow, Panel } from "../../components/workbench/Workbench";
import {
  type LevelSettings,
  type SettingsLevel,
  SectionHeader,
  SelectSetting,
  SettingField,
  TAB_OVERRIDE_KEYS,
  TextSetting,
} from "./shared";

/** Headers as stored ({name: value}) and as edited: one "Name: value" per line. */
function headersText(value: unknown): string {
  if (!value || typeof value !== "object") return "";
  return Object.entries(value as Record<string, unknown>)
    .map(([name, header]) => `${name}: ${String(header)}`)
    .join("\n");
}

function parseHeaders(text: string): Record<string, string> {
  const headers: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const at = line.indexOf(":");
    if (!line.trim()) continue;
    if (at <= 0) {
      headers[line.trim()] = "";
    } else {
      headers[line.slice(0, at).trim()] = line.slice(at + 1).trim();
    }
  }
  return headers;
}

/** The headers sent with each export, edited as lines; values name their secret as {{secret:NAME}}. */
function HeadersSetting({ level, setting, store }: { level: SettingsLevel; setting: Setting; store: LevelSettings }) {
  const stored = headersText(setting.value);
  const [value, setValue] = useState(stored);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => setValue(stored), [stored]);

  return (
    <SettingField
      level={level}
      setting={setting}
      display={(item) => headersText(item).replace(/\n/g, ", ")}
      message={message}
      onReset={() => void store.reset(setting.key).then(setMessage)}
      control={
        <form
          className="settings-inline-form"
          onSubmit={(event) => {
            event.preventDefault();
            void store.save(setting.key, parseHeaders(value)).then(setMessage);
          }}
        >
          <textarea
            aria-label={setting.label}
            className="mono"
            rows={3}
            placeholder="Authorization: Bearer {{secret:OTLP_TOKEN}}"
            value={value}
            onChange={(event) => setValue(event.target.value)}
          />
          <button type="submit" className="button" disabled={value === stored}>
            Save
          </button>
        </form>
      }
    />
  );
}

function isoDay(date: Date): string {
  return date.toISOString().slice(0, 10);
}

/** The download of the replies of a period, at Global for all environments, in an environment for its own. */
function PeriodDownload({ level }: { level: SettingsLevel }) {
  const today = new Date();
  const [start, setStart] = useState(isoDay(new Date(today.getTime() - 7 * 86_400_000)));
  const [end, setEnd] = useState(isoDay(today));
  const params = new URLSearchParams();
  if (start && end) {
    params.set("since", `${start}T00:00:00Z`);
    // The last day counts whole: the period ends at the start of the day after it.
    params.set("until", `${isoDay(new Date(Date.parse(`${end}T00:00:00Z`) + 86_400_000))}T00:00:00Z`);
  }
  if (level.kind === "environment") params.set("environment_id", String(level.environment.id));
  const ready = Boolean(start && end && start <= end);

  return (
    <Panel title="Download">
      <FieldRow label="Period" hint="Days in UTC; at most 1,000 replies">
        <div className="settings-audit-filters">
          <label>
            <span className="small muted">From</span>
            <input type="date" value={start} max={end || undefined} onChange={(event) => setStart(event.target.value)} />
          </label>
          <label>
            <span className="small muted">To</span>
            <input type="date" value={end} min={start || undefined} onChange={(event) => setEnd(event.target.value)} />
          </label>
          {ready ? (
            <a className="button button-ghost" href={`/api/v1/telemetry/otlp?${params.toString()}`} download>
              Download OTLP JSON
            </a>
          ) : (
            <button type="button" className="button button-ghost" disabled>
              Download OTLP JSON
            </button>
          )}
        </div>
        <p className="settings-help">
          Every reply that started in the period, one trace each, with prompt content only when it is included above.
        </p>
      </FieldRow>
    </Panel>
  );
}

/** Traces as OpenTelemetry (OTLP JSON): where they are sent, what they hold, and a download per period. */
export default function TelemetrySection({ level, store }: { level: SettingsLevel; store: LevelSettings }) {
  const endpoint = store.find("otlp_endpoint");
  const headers = store.find("otlp_headers");
  const content = store.find("otlp_include_content");
  return (
    <>
      <SectionHeader
        level={level}
        store={store}
        title="Telemetry"
        overrideKeys={TAB_OVERRIDE_KEYS.telemetry}
        info={
          <p>
            Each reply as an OpenTelemetry trace: its runs and sub-runs, every model call with its tokens, cost and
            duration, and every tool call, following the GenAI semantic conventions. With an endpoint, YAAP sends each
            trace to your collector after the reply, in the background; a collector that fails never holds up a reply.
            Every export is in the audit log.
          </p>
        }
      />
      <div className="wb-body wb-body-narrow wb-stack">
        <Panel title="Export" flush>
          {endpoint && (
            <TextSetting
              level={level}
              setting={endpoint}
              placeholder="http://otel-collector:4318"
              maxLength={300}
              onSave={(value) => store.save(endpoint.key, value)}
              onReset={() => store.reset(endpoint.key)}
            />
          )}
          {headers && <HeadersSetting level={level} setting={headers} store={store} />}
          {content && (
            <SelectSetting
              level={level}
              setting={content}
              choices={content.options}
              onSave={(value) => store.save(content.key, value)}
              onReset={() => store.reset(content.key)}
            />
          )}
        </Panel>
        <PeriodDownload level={level} />
      </div>
    </>
  );
}
