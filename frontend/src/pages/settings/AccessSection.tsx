import { type FormEvent, useCallback, useEffect, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";
import { signOut } from "../../auth/AuthGate";
import { Badge, EmptyState, FieldRow, Panel, Segmented } from "../../components/workbench/Workbench";
import { useEnvironment } from "../../environment/EnvironmentProvider";
import { type SettingsLevel, SectionHeader } from "./shared";
import "../../auth/auth.css";
import { useConfirm } from "../../components/dialog/ConfirmDialog";

type Token = components["schemas"]["ApiTokenOut"];
type Created = components["schemas"]["ApiTokenCreatedOut"];
type Purpose = Token["purpose"];
type Project = components["schemas"]["ProjectOut"];

const PURPOSE_HINTS: Record<Purpose, string> = {
  api: "The whole REST API, as this browser, for your own scripts. It cannot manage the password or tokens.",
  mcp: "YAAP's tools as an MCP server for an outside client, confined to an environment and optionally a project.",
  a2a: "YAAP as an A2A agent that other agents can give tasks, confined to an environment and optionally a project.",
};

function when(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString() : "never";
}

type Message = { error: boolean; text: string } | null;

/** Settings › Account › Access: the password, signing out, and API tokens. */
export default function AccessSection({ level }: { level: SettingsLevel }) {
  return (
    <>
      <SectionHeader
        level={level}
        title="Access"
        info={
          <p>
            YAAP has one account with a password. Each browser signs in with it; tokens let scripts and other
            programs in without a browser. YAAP stays bound to this computer (127.0.0.1) unless you change that in
            Docker yourself.
          </p>
        }
      />
      <div className="wb-body wb-stack">
        <PasswordPanel />
        <TokensPanel />
      </div>
    </>
  );
}

function PasswordPanel() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [message, setMessage] = useState<Message>(null);

  async function change(event: FormEvent) {
    event.preventDefault();
    if (next !== again) {
      setMessage({ error: true, text: "The two new passwords differ." });
      return;
    }
    const { error } = await api.PUT("/api/v1/auth/password", { body: { current_password: current, new_password: next } });
    if (error) {
      setMessage({ error: true, text: errorMessage(error, "The password was not changed.") });
      return;
    }
    setCurrent("");
    setNext("");
    setAgain("");
    setMessage({ error: false, text: "The password is changed. Other browsers are signed out." });
  }

  async function signOutOthers() {
    const { error } = await api.POST("/api/v1/auth/sign-out-everywhere");
    setMessage(error ? { error: true, text: errorMessage(error) } : { error: false, text: "Every other browser is signed out." });
  }

  return (
    <Panel
      title="Password"
      aside={
        <span className="wb-actions">
          <button type="button" className="button button-ghost" onClick={() => void signOutOthers()}>
            Sign out other browsers
          </button>
          <button type="button" className="button button-ghost" onClick={() => void signOut()}>
            Sign out
          </button>
        </span>
      }
    >
      <form className="wb-stack" onSubmit={change}>
        <FieldRow label="Current password">
          <input type="password" autoComplete="current-password" value={current} onChange={(event) => setCurrent(event.target.value)} />
        </FieldRow>
        <FieldRow label="New password" hint="At least 10 characters.">
          <input type="password" autoComplete="new-password" value={next} onChange={(event) => setNext(event.target.value)} />
        </FieldRow>
        <FieldRow label="New password again">
          <input type="password" autoComplete="new-password" value={again} onChange={(event) => setAgain(event.target.value)} />
        </FieldRow>
        <div>
          <button type="submit" className="button button-primary" disabled={!current || !next}>
            Change password
          </button>
        </div>
        {message && <p className={message.error ? "form-error" : "muted"}>{message.text}</p>}
        <p className="faint small">
          Forgot it? From the YAAP folder: <code>docker compose exec -it yaap python -m app.scripts.set_password --reset</code>
        </p>
      </form>
    </Panel>
  );
}

function TokensPanel() {
  const confirm = useConfirm();
  const { environments } = useEnvironment();
  const [tokens, setTokens] = useState<Token[] | null>(null);
  const [created, setCreated] = useState<Created | null>(null);
  const [message, setMessage] = useState<Message>(null);
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState<Purpose>("mcp");
  const [environmentId, setEnvironmentId] = useState<number | null>(environments[0]?.id ?? null);
  const [projectId, setProjectId] = useState<number | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);

  const load = useCallback(async () => {
    const { data } = await api.GET("/api/v1/auth/tokens");
    setTokens(data ?? []);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    setProjectId(null);
    if (environmentId === null) {
      setProjects([]);
      return;
    }
    void api
      .GET("/api/v1/environments/{environment_id}/projects", { params: { path: { environment_id: environmentId } } })
      .then(({ data }) => setProjects(data ?? []));
  }, [environmentId]);

  async function create(event: FormEvent) {
    event.preventDefault();
    const confined = purpose !== "api";
    const { data, error } = await api.POST("/api/v1/auth/tokens", {
      body: {
        name: name.trim(),
        purpose,
        environment_id: confined ? environmentId : null,
        project_id: confined ? projectId : null,
        description: "",
      },
    });
    if (error || !data) {
      setMessage({ error: true, text: errorMessage(error, "The token was not made.") });
      return;
    }
    setMessage(null);
    setCreated(data);
    setName("");
    await load();
  }

  async function revoke(token: Token) {
    const question = `Revoke ${token.name}? Whatever uses it stops working at once.`;
    if (!(await confirm({ message: question, confirmLabel: "Revoke", tone: "danger" }))) return;
    const { error } = await api.DELETE("/api/v1/auth/tokens/{token_id}", { params: { path: { token_id: token.id } } });
    if (error) {
      setMessage({ error: true, text: errorMessage(error) });
      return;
    }
    if (created?.id === token.id) setCreated(null);
    await load();
  }

  return (
    <Panel title="API tokens" flush>
      {created && (
        <div className="wb-stack" style={{ padding: "12px 16px" }}>
          <div className="token-reveal" role="status">
            <strong>Copy the token for {created.name} now: YAAP shows it only once.</strong>
            <code>{created.token}</code>
            <span>
              <button type="button" className="button" onClick={() => void navigator.clipboard?.writeText(created.token)}>
                Copy
              </button>{" "}
              <button type="button" className="button button-ghost" onClick={() => setCreated(null)}>
                Done
              </button>
            </span>
            <span className="faint small">Send it as the header Authorization: Bearer &lt;token&gt;.</span>
          </div>
        </div>
      )}
      {tokens === null ? (
        <EmptyState>Loading…</EmptyState>
      ) : tokens.length === 0 ? (
        <EmptyState>No tokens yet. Only signed-in browsers reach YAAP.</EmptyState>
      ) : (
        <div className="wb-table-scroll">
          <table className="wb-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>For</th>
                <th>Confined to</th>
                <th>Last used</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {tokens.map((token) => (
                <tr key={token.id}>
                  <td>
                    {token.name}
                    {token.expires_at && new Date(token.expires_at) < new Date() && (
                      <>
                        {" "}
                        <Badge tone="danger">expired</Badge>
                      </>
                    )}
                  </td>
                  <td>
                    <Badge>{token.purpose_label}</Badge>
                  </td>
                  <td className="small">{token.scope_name}</td>
                  <td className="small">{when(token.last_used_at)}</td>
                  <td>
                    <button type="button" className="button button-danger" onClick={() => void revoke(token)}>
                      Revoke
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <form className="wb-stack" style={{ padding: "12px 16px" }} onSubmit={create}>
        <FieldRow label="For" hint={PURPOSE_HINTS[purpose]}>
          <Segmented<Purpose>
            label="Purpose"
            value={purpose}
            onChange={setPurpose}
            options={[
              { value: "mcp", label: "MCP server" },
              { value: "a2a", label: "A2A agent" },
              { value: "api", label: "REST API" },
            ]}
          />
        </FieldRow>
        <FieldRow label="Name" hint="Who uses it, for example claude-desktop or nightly-script.">
          <input value={name} maxLength={64} onChange={(event) => setName(event.target.value)} />
        </FieldRow>
        {purpose !== "api" && (
          <>
            <FieldRow label="Environment">
              <select value={environmentId ?? ""} onChange={(event) => setEnvironmentId(Number(event.target.value) || null)}>
                {environments.map((environment) => (
                  <option key={environment.id} value={environment.id}>
                    {environment.name}
                  </option>
                ))}
              </select>
            </FieldRow>
            <FieldRow label="Project" hint="Optional: without one, only what needs no project folder.">
              <select value={projectId ?? ""} onChange={(event) => setProjectId(Number(event.target.value) || null)}>
                <option value="">Whole environment</option>
                {projects.map((project) => (
                  <option key={project.id} value={project.id}>
                    {project.name}
                  </option>
                ))}
              </select>
            </FieldRow>
          </>
        )}
        <div>
          <button type="submit" className="button button-primary" disabled={!name.trim()}>
            Make token
          </button>
        </div>
        {message && <p className={message.error ? "form-error" : "muted"}>{message.text}</p>}
      </form>
    </Panel>
  );
}
