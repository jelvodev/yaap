import { useCallback, useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { components } from "../api/schema";

type Server = components["schemas"]["McpServerOut"];

const EXAMPLE = `{
  "github": {
    "transport": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-github"],
    "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "{{secret:GITHUB_TOKEN}}" },
    "auto_tools": ["search_repositories"]
  },
  "docs": { "transport": "http", "url": "https://example.com/mcp" }
}`;

const STATE_LABELS: Record<Server["state"], string> = { running: "Running", stopped: "Not started", failed: "Failed" };

function ServerRow({ server, onTest }: { server: Server; onTest: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [testing, setTesting] = useState(false);
  return (
    <li className="mcp-server">
      <div className="mcp-server-head">
        <button type="button" className="link-button" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
          {server.name}
        </button>
        <span className="faint small mono">{server.transport}</span>
        <span className={`mcp-state mcp-state-${server.state}`}>{STATE_LABELS[server.state]}</span>
        <span className="faint small">{server.tools.length ? `${server.tools.length} tools` : ""}</span>
        <button
          type="button"
          className="button button-ghost"
          disabled={testing}
          onClick={async () => {
            setTesting(true);
            await onTest();
            setTesting(false);
            setOpen(true);
          }}
        >
          {testing ? "Starting…" : "Test"}
        </button>
      </div>
      {server.error && <p className="form-error small">{server.error}</p>}
      {open && (
        <div className="mcp-server-body">
          <p className="faint small mono">{server.target}</p>
          {server.tools.length > 0 ? (
            <ul className="mcp-tools">
              {server.tools.map((tool) => (
                <li key={tool.name} className={tool.disabled ? "faint" : undefined}>
                  <span className="mono">{tool.model_name}</span>
                  {tool.disabled ? " · off" : tool.auto ? " · runs without asking" : " · asks"}
                  {tool.description && <span className="faint small"> — {tool.description}</span>}
                </li>
              ))}
            </ul>
          ) : (
            <p className="faint small">Tools show once the server has started (Test, or the agent's first use).</p>
          )}
          {server.log.length > 0 && <pre className="mcp-log">{server.log.join("\n")}</pre>}
        </div>
      )}
    </li>
  );
}

/**
 * Outside MCP servers: this level's own servers as JSON, and the status, tools and log of every
 * server that applies. A project adds servers or overrides one by name; null switches an inherited one off.
 */
export default function McpServersPanel({ environmentId, projectId }: { environmentId: number; projectId?: number }) {
  const [text, setText] = useState("");
  const [saved, setSaved] = useState("");
  const [servers, setServers] = useState<Server[]>([]);
  const [message, setMessage] = useState<{ error: boolean; text: string } | null>(null);

  const loadSetting = useCallback(async () => {
    const { data } =
      projectId !== undefined
        ? await api.GET("/api/v1/projects/{project_id}/settings", { params: { path: { project_id: projectId } } })
        : await api.GET("/api/v1/environments/{environment_id}/settings", { params: { path: { environment_id: environmentId } } });
    const setting = data?.find((item) => item.key === "mcp_servers");
    const own = setting?.level_value;
    const value = own && typeof own === "object" && Object.keys(own).length ? JSON.stringify(own, null, 2) : "";
    setText(value);
    setSaved(value);
  }, [environmentId, projectId]);

  const loadServers = useCallback(async () => {
    const { data } = await api.GET("/api/v1/environments/{environment_id}/mcp", {
      params: { path: { environment_id: environmentId }, query: { project_id: projectId ?? null } },
    });
    setServers(data ?? []);
  }, [environmentId, projectId]);

  useEffect(() => {
    void loadSetting();
    void loadServers();
  }, [loadSetting, loadServers]);

  async function save() {
    let value: unknown = {};
    if (text.trim()) {
      try {
        value = JSON.parse(text);
      } catch {
        setMessage({ error: true, text: "This is not valid JSON." });
        return;
      }
    }
    const { error } =
      projectId !== undefined
        ? await api.PUT("/api/v1/projects/{project_id}/settings/{key}", {
            params: { path: { project_id: projectId, key: "mcp_servers" } },
            body: { value },
          })
        : await api.PUT("/api/v1/environments/{environment_id}/settings/{key}", {
            params: { path: { environment_id: environmentId, key: "mcp_servers" } },
            body: { value },
          });
    if (error) {
      setMessage({ error: true, text: errorMessage(error) });
      return;
    }
    setMessage({ error: false, text: "Saved. New replies use these servers." });
    await loadSetting();
    await loadServers();
  }

  async function test(name: string) {
    const { data, error } = await api.POST("/api/v1/environments/{environment_id}/mcp/{name}/test", {
      params: { path: { environment_id: environmentId, name }, query: { project_id: projectId ?? null } },
    });
    if (error || !data) {
      setMessage({ error: true, text: errorMessage(error) });
      return;
    }
    setServers((current) => current.map((server) => (server.name === name ? data : server)));
  }

  return (
    <div className="mcp-panel">
      {servers.length > 0 ? (
        <ul className="mcp-servers">
          {servers.map((server) => (
            <ServerRow key={server.name} server={server} onTest={() => test(server.name)} />
          ))}
        </ul>
      ) : (
        <p className="faint small">No MCP servers{projectId !== undefined ? " for this project" : ""} yet.</p>
      )}
      <label className="faint small" htmlFor={`mcp-${environmentId}-${projectId ?? "env"}`}>
        {projectId !== undefined ? "This project's servers (added to or overriding the environment's)" : "Servers of this environment"}{" "}
        as JSON. Use {"{{secret:NAME}}"} for tokens in env and headers. Tools ask before each call unless listed in auto_tools.
      </label>
      <textarea
        id={`mcp-${environmentId}-${projectId ?? "env"}`}
        className="mono mcp-config"
        rows={Math.min(18, Math.max(6, text.split("\n").length + 1))}
        placeholder={EXAMPLE}
        value={text}
        spellCheck={false}
        onChange={(event) => setText(event.target.value)}
      />
      <div className="pp-row">
        <button type="button" className="button button-primary" disabled={text === saved} onClick={() => void save()}>
          Save servers
        </button>
        {text !== saved && (
          <button type="button" className="button button-ghost" onClick={() => setText(saved)}>
            Undo
          </button>
        )}
      </div>
      {message && <p className={message.error ? "form-error small" : "muted small"}>{message.text}</p>}
    </div>
  );
}
