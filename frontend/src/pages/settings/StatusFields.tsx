import { useCallback, useEffect, useState } from "react";

import { type ClaudeCodeStatus, type CodexStatus, api } from "../../api/client";
import type { components } from "../../api/schema";
import { Badge, FieldRow } from "../../components/workbench/Workbench";

type CliStatus = {
  installed: boolean;
  logged_in: boolean;
  version?: string | null;
  sign_in_command: string;
};

/** Whether a vendor CLI in this YAAP container (Claude Code, Codex) is signed in. One sign-in serves every environment. */
function CliStatusField<T extends CliStatus>({
  name,
  load,
  account,
  signInHelp,
}: {
  name: string;
  load: () => Promise<T | null>;
  account: (status: T) => string;
  signInHelp: string;
}) {
  const [status, setStatus] = useState<T | null>(null);
  const [checking, setChecking] = useState(false);

  const check = useCallback(async () => {
    setChecking(true);
    setStatus(await load());
    setChecking(false);
  }, [load]);

  useEffect(() => {
    void check();
  }, [check]);

  return (
    <FieldRow label={name} hint="Shared by every environment">
      {!status ? (
        <span className="settings-inherited">{checking ? `Checking ${name}…` : `${name} status unknown`}</span>
      ) : (
        <Badge tone={!status.installed ? "danger" : status.logged_in ? "ok" : "warn"}>
          {!status.installed ? "not installed" : status.logged_in ? "signed in" : "not signed in"}
        </Badge>
      )}
      {status?.logged_in && (
        <span className="settings-inherited">
          {account(status)} {status.version ? `· ${status.version}` : ""}
        </span>
      )}
      <button type="button" className="button button-ghost" disabled={checking} onClick={() => void check()}>
        {checking ? "Checking…" : "Check again"}
      </button>
      {status?.installed && !status.logged_in && (
        <>
          <p className="settings-help">{signInHelp}</p>
          <pre className="command-line settings-command">{status.sign_in_command}</pre>
        </>
      )}
      {status && !status.installed && (
        <p className="settings-help">Rebuild the YAAP image to install {name}: docker compose up -d --build</p>
      )}
    </FieldRow>
  );
}

export async function loadClaudeCodeStatus(): Promise<ClaudeCodeStatus | null> {
  const { data } = await api.GET("/api/v1/claude-code/status");
  return data ?? null;
}

export async function loadCodexStatus(): Promise<CodexStatus | null> {
  const { data } = await api.GET("/api/v1/codex/status");
  return data ?? null;
}

export function ClaudeCodeStatusField() {
  return (
    <CliStatusField
      name="Claude Code"
      load={loadClaudeCodeStatus}
      account={(status) => [status.subscription_type, status.email].filter(Boolean).join(" · ") || status.auth_method || ""}
      signInHelp={
        "Sign in once with your Claude account. Run this in a terminal in the YAAP folder, open the link it shows, " +
        "and paste the code back. The sign-in stays in YAAP's data folder and serves every environment."
      }
    />
  );
}

export function CodexStatusField() {
  return (
    <CliStatusField
      name="Codex"
      load={loadCodexStatus}
      account={(status) => status.detail ?? ""}
      signInHelp={
        "Sign in once with your ChatGPT account. Run this in a terminal in the YAAP folder, open the link it shows " +
        "and enter the code there. The sign-in stays in YAAP's data folder and serves every environment."
      }
    />
  );
}

/** How far the search index is: the model in use and the messages it has not reached yet. */
export function SearchIndexStatus({ setting }: { setting: string }) {
  const [status, setStatus] = useState<components["schemas"]["SearchIndexStatusOut"] | null>(null);

  useEffect(() => {
    let current = true;
    const load = () =>
      void api.GET("/api/v1/search-index/status").then(({ data }) => current && setStatus(data ?? null));
    load();
    const timer = window.setInterval(load, 10_000);
    return () => {
      current = false;
      window.clearInterval(timer);
    };
  }, [setting]);

  if (!status) return <span className="settings-inherited">Checking the index…</span>;
  if (!status.model) {
    return (
      <>
        <Badge tone="off">semantic search off</Badge>
        <span className="settings-inherited">
          {setting === "off"
            ? "Search finds words only."
            : "The model is not available in this installation; search finds words only."}
        </span>
      </>
    );
  }
  return (
    <>
      <Badge tone={status.waiting_messages > 0 ? "warn" : "ok"}>
        {status.waiting_messages > 0 ? "indexing" : "up to date"}
      </Badge>
      <span className="settings-inherited">
        <span className="mono">{status.model}</span> · {status.chunks.toLocaleString()} pieces indexed
        {status.waiting_messages > 0 &&
          ` · ${status.waiting_messages.toLocaleString()} messages waiting, indexed in the background`}
      </span>
    </>
  );
}
