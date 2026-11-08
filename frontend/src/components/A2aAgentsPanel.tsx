import { useCallback, useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { components } from "../api/schema";

type Card = components["schemas"]["A2aAgentCardOut"];

const EXAMPLE = `{
  "research": {
    "url": "https://agents.example.com/research",
    "token": "{{secret:RESEARCH_AGENT_TOKEN}}",
    "description": "Finds and summarises papers."
  }
}`;

function AgentRow({ environmentId, name, url }: { environmentId: number; name: string; url: string }) {
  const [testing, setTesting] = useState(false);
  const [card, setCard] = useState<Card | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function test() {
    setTesting(true);
    setError(null);
    const { data, error: requestError } = await api.POST("/api/v1/environments/{environment_id}/a2a/{name}/test", {
      params: { path: { environment_id: environmentId, name } },
    });
    setTesting(false);
    if (requestError || !data) {
      setCard(null);
      setError(errorMessage(requestError, "The agent could not be reached."));
      return;
    }
    setCard(data);
  }

  return (
    <li className="mcp-server">
      <div className="mcp-server-head">
        <span>{name}</span>
        <span className="faint small mono">{url}</span>
        <button type="button" className="button button-ghost" disabled={testing} onClick={() => void test()}>
          {testing ? "Reading…" : "Test"}
        </button>
      </div>
      {error && <p className="form-error small">{error}</p>}
      {card && (
        <div className="mcp-server-body">
          <p className="small">
            {card.name || name}
            {card.description && <span className="faint"> — {card.description}</span>}
          </p>
          {card.skills.length > 0 && <p className="faint small">Skills: {card.skills.join(", ")}</p>}
        </div>
      )}
    </li>
  );
}

/**
 * Remote A2A agents: this environment's agents as JSON, each with a test that reads its agent card.
 * The agent asks before every call, and the answer counts as outside content.
 */
export default function A2aAgentsPanel({ environmentId }: { environmentId: number }) {
  const [text, setText] = useState("");
  const [saved, setSaved] = useState("");
  const [agents, setAgents] = useState<[string, string][]>([]);
  const [message, setMessage] = useState<{ error: boolean; text: string } | null>(null);

  const load = useCallback(async () => {
    const { data } = await api.GET("/api/v1/environments/{environment_id}/settings", {
      params: { path: { environment_id: environmentId } },
    });
    const own = data?.find((item) => item.key === "a2a_agents")?.level_value;
    const value = own && typeof own === "object" && Object.keys(own).length ? JSON.stringify(own, null, 2) : "";
    setText(value);
    setSaved(value);
    const entries = own && typeof own === "object" ? Object.entries(own as Record<string, { url?: string; enabled?: boolean }>) : [];
    setAgents(entries.filter(([, agent]) => agent?.enabled !== false).map(([name, agent]) => [name, agent?.url ?? ""]));
  }, [environmentId]);

  useEffect(() => {
    void load();
  }, [load]);

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
    const { error } = await api.PUT("/api/v1/environments/{environment_id}/settings/{key}", {
      params: { path: { environment_id: environmentId, key: "a2a_agents" } },
      body: { value },
    });
    if (error) {
      setMessage({ error: true, text: errorMessage(error) });
      return;
    }
    setMessage({ error: false, text: "Saved. New replies can ask these agents." });
    await load();
  }

  return (
    <div className="mcp-panel">
      {agents.length > 0 ? (
        <ul className="mcp-servers">
          {agents.map(([name, url]) => (
            <AgentRow key={name} environmentId={environmentId} name={name} url={url} />
          ))}
        </ul>
      ) : (
        <p className="faint small">No A2A agents yet.</p>
      )}
      <label className="faint small" htmlFor={`a2a-${environmentId}`}>
        Agents of this environment as JSON: url (the agent card, or where it is found), an optional token as{" "}
        {"{{secret:NAME}}"} from the vault, a description for the agent, timeout in seconds and enabled. Each call asks
        first.
      </label>
      <textarea
        id={`a2a-${environmentId}`}
        className="mono mcp-config"
        rows={Math.min(18, Math.max(6, text.split("\n").length + 1))}
        placeholder={EXAMPLE}
        value={text}
        spellCheck={false}
        onChange={(event) => setText(event.target.value)}
      />
      <div className="pp-row">
        <button type="button" className="button button-primary" disabled={text === saved} onClick={() => void save()}>
          Save agents
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
