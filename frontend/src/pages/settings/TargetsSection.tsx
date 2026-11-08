import { type FormEvent, useCallback, useEffect, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";
import { Badge, EmptyState, FieldRow, Panel, Segmented } from "../../components/workbench/Workbench";
import RunnerPanel from "./RunnerPanel";
import { type SettingsLevel, SectionHeader } from "./shared";

type Target = components["schemas"]["TargetOut"];
type Rights = "standard" | "administrator";

const SYSTEMS: Record<string, string> = { windows: "Windows", linux: "Linux", macos: "macOS", unknown: "not tested yet" };

const STATE_BADGE: Record<Target["state"], { tone: "ok" | "warn" | "danger" | "neutral"; label: string }> = {
  new: { tone: "neutral", label: "not tested" },
  confirm: { tone: "warn", label: "confirm host key" },
  ready: { tone: "ok", label: "ready" },
  problem: { tone: "danger", label: "problem" },
};

function folderLines(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

/** Settings › Targets: computers the agent can work on over SSH, such as the one that runs YAAP. */
export default function TargetsSection({ level }: { level: SettingsLevel }) {
  const environmentId = level.kind === "environment" ? level.environment.id : 0;
  const [targets, setTargets] = useState<Target[] | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [message, setMessage] = useState<{ error: boolean; text: string } | null>(null);
  const [draft, setDraft] = useState({ name: "This computer", host: "host.docker.internal", port: 22, user: "yaap", folders: "" });

  const load = useCallback(async () => {
    const { data } = await api.GET("/api/v1/environments/{environment_id}/targets", {
      params: { path: { environment_id: environmentId } },
    });
    setTargets(data ?? []);
  }, [environmentId]);

  useEffect(() => {
    void load();
  }, [load]);

  function replaceTarget(target: Target) {
    setTargets((current) => (current ?? []).map((item) => (item.id === target.id ? target : item)));
  }

  async function add(event: FormEvent) {
    event.preventDefault();
    const { data, error } = await api.POST("/api/v1/environments/{environment_id}/targets", {
      params: { path: { environment_id: environmentId } },
      body: {
        name: draft.name.trim(),
        host: draft.host.trim(),
        port: draft.port,
        user: draft.user.trim(),
        folders: folderLines(draft.folders),
      },
    });
    if (error || !data) {
      setMessage({ error: true, text: errorMessage(error, "The target could not be added.") });
      return;
    }
    setMessage({ error: false, text: `${data.name} added. Run its setup script, then press Test.` });
    setDraft({ ...draft, name: "", folders: "" });
    await load();
    setOpen(data.id);
  }

  if (level.kind !== "environment") return null;
  return (
    <>
      <SectionHeader
        level={level}
        title="Targets"
        info={
          <p>
            Computers the agent can work on over SSH instead of in this environment&apos;s runner, such as the computer that
            runs YAAP. YAAP signs in with its own key, only after you confirmed the computer&apos;s host key. Every
            change there asks first, and cannot be rolled back in YAAP. A project chooses its target in its settings;
            a conversation outside a project chooses one before its first message.
          </p>
        }
      />
      <div className="wb-body wb-stack">
        <RunnerPanel environmentId={environmentId} />
        <Panel title="Computers" flush>
          {targets === null ? (
            <EmptyState>Loading…</EmptyState>
          ) : targets.length === 0 ? (
            <EmptyState>
              No targets yet. The agent&apos;s commands run in the runner above. Add this computer below to let it work in your
              own folders.
            </EmptyState>
          ) : (
            <div className="wb-table-scroll">
              <table className="wb-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Address</th>
                    <th>System</th>
                    <th>State</th>
                    <th>Switches</th>
                  </tr>
                </thead>
                <tbody>
                  {targets.map((target) => (
                    <tr key={target.id}>
                      <td>
                        <button
                          type="button"
                          className="link-button"
                          aria-expanded={open === target.id}
                          onClick={() => setOpen(open === target.id ? null : target.id)}
                        >
                          {target.name}
                        </button>
                      </td>
                      <td className="mono small">
                        {target.user}@{target.host}:{target.port}
                      </td>
                      <td className="small">
                        {SYSTEMS[target.system] ?? target.system}
                        {target.rights !== "unknown" && `, ${target.rights} user`}
                      </td>
                      <td>
                        <Badge tone={STATE_BADGE[target.state].tone}>{STATE_BADGE[target.state].label}</Badge>
                      </td>
                      <td>
                        {target.allow_full_access && <Badge tone="danger">full access allowed</Badge>}{" "}
                        {target.whole_computer && <Badge tone="warn">whole computer</Badge>}
                        {!target.allow_full_access && !target.whole_computer && <span className="faint small">off</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {message && <p className={`small target-message${message.error ? " form-error" : ""}`}>{message.text}</p>}
        </Panel>

        {targets?.filter((target) => target.id === open).map((target) => (
          <TargetDetail
            key={target.id}
            target={target}
            onChange={replaceTarget}
            onRemoved={() => {
              setOpen(null);
              setMessage({ error: false, text: `${target.name} removed, with its key.` });
              void load();
            }}
          />
        ))}

        <Panel title="Add a computer" flush>
          <form className="target-add" onSubmit={(event) => void add(event)}>
            <FieldRow label="Name">
              <input value={draft.name} maxLength={80} required onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
            </FieldRow>
            <FieldRow label="Address" hint="host.docker.internal is the computer that runs Docker">
              <span className="target-address">
                <input
                  aria-label="User"
                  className="mono"
                  value={draft.user}
                  onChange={(event) => setDraft({ ...draft, user: event.target.value })}
                />
                <span>@</span>
                <input
                  aria-label="Host"
                  className="mono"
                  value={draft.host}
                  onChange={(event) => setDraft({ ...draft, host: event.target.value })}
                />
                <span>:</span>
                <input
                  aria-label="Port"
                  type="number"
                  min={1}
                  max={65535}
                  value={draft.port}
                  onChange={(event) => setDraft({ ...draft, port: Number(event.target.value) })}
                />
              </span>
            </FieldRow>
            <FieldRow label="Folders" hint="One per line; the agent works only inside these">
              <textarea
                aria-label="Folders"
                className="mono"
                rows={3}
                placeholder={"C:\\Users\\you\\Projects"}
                value={draft.folders}
                onChange={(event) => setDraft({ ...draft, folders: event.target.value })}
              />
            </FieldRow>
            <div className="settings-inline-form">
              <button type="submit" className="button" disabled={!draft.name.trim() || !draft.host.trim()}>
                Add computer
              </button>
            </div>
          </form>
        </Panel>
      </div>
    </>
  );
}

function TargetDetail({
  target,
  onChange,
  onRemoved,
}: {
  target: Target;
  onChange: (target: Target) => void;
  onRemoved: () => void;
}) {
  const [rights, setRights] = useState<Rights>(target.rights === "administrator" ? "administrator" : "standard");
  const [script, setScript] = useState<string | null>(null);
  const [folders, setFolders] = useState(target.folders.join("\n"));
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ error: boolean; text: string } | null>(null);
  const path = { params: { path: { target_id: target.id } } };

  useEffect(() => {
    let current = true;
    void api
      .GET("/api/v1/targets/{target_id}/setup-script", {
        params: { path: { target_id: target.id }, query: { rights } },
        parseAs: "text",
      })
      .then(({ data }) => current && setScript(typeof data === "string" ? data : null));
    return () => {
      current = false;
    };
  }, [target.id, rights]);

  async function run(action: () => Promise<{ data?: Target; error?: unknown }>, done?: string) {
    setBusy(true);
    setNote(null);
    const { data, error } = await action();
    setBusy(false);
    if (error || !data) {
      setNote({ error: true, text: errorMessage(error, "That did not work.") });
      return;
    }
    onChange(data);
    if (done) setNote({ error: false, text: done });
  }

  const test = () => run(() => api.POST("/api/v1/targets/{target_id}/test", path));
  const confirmKey = () =>
    run(() =>
      api.POST("/api/v1/targets/{target_id}/host-key", { ...path, body: { fingerprint: target.pending_fingerprint ?? "" } }),
    );

  function switchFullAccess() {
    if (target.allow_full_access) {
      void run(() => api.PATCH("/api/v1/targets/{target_id}", { ...path, body: { allow_full_access: false, confirm_full_access: false } }), "Full access is off.");
      return;
    }
    const who = target.rights === "administrator" ? "an administrator" : target.rights === "standard" ? "a standard user" : "a user whose rights were not tested yet";
    if (
      !window.confirm(
        `Allow full access on ${target.name}? Conversations with the access choice "full" then run every tool there without asking, as ${who}. Only pushing and commands with a secret still ask. Scheduled tasks never get full access.`,
      )
    )
      return;
    void run(
      () => api.PATCH("/api/v1/targets/{target_id}", { ...path, body: { allow_full_access: true, confirm_full_access: true } }),
      "Full access is allowed.",
    );
  }

  function switchWholeComputer() {
    if (
      !target.whole_computer &&
      !window.confirm(`Let the agent work anywhere on ${target.name}, not only in its folders?`)
    )
      return;
    void run(() => api.PATCH("/api/v1/targets/{target_id}", { ...path, body: { whole_computer: !target.whole_computer, confirm_full_access: false } }));
  }

  async function remove() {
    if (!window.confirm(`Remove ${target.name} and its key? Conversations on it go back to YAAP's container.`)) return;
    const { error } = await api.DELETE("/api/v1/targets/{target_id}", path);
    if (error) {
      setNote({ error: true, text: errorMessage(error, "The target could not be removed.") });
      return;
    }
    onRemoved();
  }

  return (
    <Panel title={target.name} flush aside={target.last_test_result ?? "Not tested yet"}>
      <div className="target-detail">
        <section>
          <h4>1. Let YAAP in</h4>
          <p className="small">
            Run this script in PowerShell as administrator on the computer. It installs OpenSSH Server, creates the
            user <span className="mono">{target.user}</span> that signs in with YAAP&apos;s key only, and opens port 22
            for Docker only. Read it first.
          </p>
          <Segmented
            label="Rights of the user"
            value={rights}
            onChange={setRights}
            options={[
              { value: "standard", label: "Standard user" },
              { value: "administrator", label: "Administrator" },
            ]}
          />
          <p className="faint small">
            {rights === "standard"
              ? "May change only the listed folders. Recommended."
              : "Joins Administrators: its SSH sessions have full administrator rights, without a UAC prompt."}
          </p>
          {script && (
            <>
              <pre className="target-script mono">{script}</pre>
              <button type="button" className="button button-ghost" onClick={() => void navigator.clipboard.writeText(script)}>
                Copy script
              </button>
            </>
          )}
          <p className="faint small">
            On Linux or macOS add this public key to <span className="mono">~/.ssh/authorized_keys</span> of the user
            instead: <span className="mono target-key">{target.public_key}</span>
          </p>
        </section>

        <section>
          <h4>2. Test and confirm</h4>
          {target.state === "confirm" && target.pending_fingerprint ? (
            <div className="target-confirm">
              <p className="small">
                The computer answered with this host key. Check it on the computer with{" "}
                <span className="mono">ssh-keygen -lf $env:ProgramData\ssh\ssh_host_ed25519_key.pub</span> before you
                confirm it:
              </p>
              <p className="mono">{target.pending_fingerprint}</p>
              <button type="button" className="button" disabled={busy} onClick={() => void confirmKey()}>
                Confirm this host key
              </button>
            </div>
          ) : (
            <p className="small">
              {target.host_fingerprint ? (
                <>
                  Host key <span className="mono">{target.host_fingerprint}</span>
                </>
              ) : (
                "No host key confirmed yet."
              )}
            </p>
          )}
          <button type="button" className="button button-ghost" disabled={busy} onClick={() => void test()}>
            {busy ? "Testing…" : "Test"}
          </button>
        </section>

        <section>
          <h4>3. Folders and switches</h4>
          <FieldRow label="Folders" hint="One per line">
            <textarea
              aria-label="Folders of the target"
              className="mono"
              rows={3}
              value={folders}
              onChange={(event) => setFolders(event.target.value)}
            />
          </FieldRow>
          <div className="settings-inline-form">
            <button
              type="button"
              className="button button-ghost"
              disabled={folders === target.folders.join("\n")}
              onClick={() =>
                void run(
                  () => api.PATCH("/api/v1/targets/{target_id}", { ...path, body: { folders: folderLines(folders), confirm_full_access: false } }),
                  "Folders saved. A standard user needs the setup script again for new folders.",
                )
              }
            >
              Save folders
            </button>
          </div>
          <FieldRow label="Allow full access" hint="Conversations with access full run every tool here without asking">
            <label className="checkbox-row">
              <input type="checkbox" checked={target.allow_full_access} disabled={busy} onChange={switchFullAccess} />
              {target.allow_full_access ? "On" : "Off"}
            </label>
          </FieldRow>
          <FieldRow label="Whole computer" hint="File tools may use any folder, not only the listed ones">
            <label className="checkbox-row">
              <input type="checkbox" checked={target.whole_computer} disabled={busy} onChange={switchWholeComputer} />
              {target.whole_computer ? "On" : "Off"}
            </label>
          </FieldRow>
        </section>

        {note && <p className={`small${note.error ? " form-error" : ""}`}>{note.text}</p>}
        <div>
          <button type="button" className="button button-ghost danger-link" onClick={() => void remove()}>
            Remove target
          </button>
        </div>
      </div>
    </Panel>
  );
}
