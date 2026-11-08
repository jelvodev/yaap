import { type KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { type ConversationSummary, api } from "../api/client";
import type { components } from "../api/schema";
import Snippet from "./Snippet";
import { useEnvironment } from "../environment/EnvironmentProvider";
import { ChatIcon, LayersIcon, PlusIcon, SearchIcon, SettingsIcon } from "./icons";

type SearchHit = components["schemas"]["SearchHitOut"];

interface PaletteAction {
  id: string;
  section: string;
  label: React.ReactNode;
  // What the query is matched against; message hits come from the server and are always shown.
  match?: string;
  icon: React.ReactNode;
  run: () => void;
}

export default function CommandPalette({ onClose }: { onClose: () => void }) {
  const { environments, active, selectEnvironment } = useEnvironment();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [hits, setHits] = useState<SearchHit[]>([]);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    inputRef.current?.focus();
    if (!active) return;
    void api
      .GET("/api/v1/environments/{environment_id}/conversations", {
        params: { path: { environment_id: active.id } },
      })
      .then(({ data }) => setConversations(data ?? []));
  }, [active]);

  // Message text is searched on the server once the query settles.
  useEffect(() => {
    const needle = query.trim();
    if (!active || needle.length < 2) {
      setHits([]);
      return;
    }
    let current = true;
    const timer = window.setTimeout(() => {
      void api
        .GET("/api/v1/environments/{environment_id}/search", {
          params: { path: { environment_id: active.id }, query: { q: needle, limit: 6, kind: ["message"] } },
        })
        .then(({ data }) => current && setHits(data ?? []));
    }, 200);
    return () => {
      current = false;
      window.clearTimeout(timer);
    };
  }, [query, active]);

  const actions = useMemo<PaletteAction[]>(() => {
    const go = (path: string) => () => {
      navigate(path);
      onClose();
    };
    const commands: PaletteAction[] = [
      { id: "new", section: "Actions", label: "New conversation", icon: <PlusIcon />, run: go("/chat") },
      { id: "blueprint", section: "Actions", label: "Open Blueprint", icon: <LayersIcon />, run: go("/blueprint") },
      {
        id: "agents",
        section: "Actions",
        label: "Agents · Blueprint",
        match: "agents runs blueprint",
        icon: <LayersIcon />,
        run: go("/blueprint?kind=agent"),
      },
      { id: "settings", section: "Actions", label: "Open Settings", icon: <SettingsIcon />, run: go("/settings") },
      {
        id: "git",
        section: "Actions",
        label: "Git and GitHub · Settings",
        match: "git github repositories push pull settings",
        icon: <SettingsIcon />,
        run: go("/settings/git"),
      },
      {
        id: "usage",
        section: "Actions",
        label: "Usage and budget · Settings",
        match: "usage budget cost settings",
        icon: <SettingsIcon />,
        run: go("/settings/usage"),
      },
      {
        id: "models",
        section: "Actions",
        label: "Models · Settings",
        match: "models presets settings",
        icon: <SettingsIcon />,
        run: go("/settings/models"),
      },
      {
        id: "connections",
        section: "Actions",
        label: "Connections · Settings",
        match: "connections providers api keys claude code codex settings",
        icon: <SettingsIcon />,
        run: go("/settings/connections"),
      },
      { id: "search", section: "Actions", label: "Search messages", icon: <SearchIcon />, run: go("/search") },
      ...environments
        .filter((environment) => environment.id !== active?.id)
        .map((environment) => ({
          id: `environment-${environment.id}`,
          section: "Actions",
          label: `Switch to ${environment.name}`,
          icon: <LayersIcon />,
          run: () => {
            selectEnvironment(environment.id);
            navigate("/chat");
            onClose();
          },
        })),
      ...conversations.map((conversation) => ({
        id: `conversation-${conversation.id}`,
        section: `Conversations · ${active?.name ?? ""}`,
        label: conversation.title,
        icon: <ChatIcon />,
        run: go(`/chat/${conversation.id}`),
      })),
    ];
    const needle = query.trim().toLowerCase();
    if (!needle) return commands;
    const matching = commands.filter((action) => String(action.match ?? action.label).toLowerCase().includes(needle));
    const messages: PaletteAction[] = hits.map((hit) => ({
      id: `message-${hit.message_id}`,
      section: "Messages",
      label: (
        <span className="palette-hit">
          <span>{hit.conversation_title}</span>
          <span className="faint small">
            <Snippet text={hit.snippet} />
          </span>
        </span>
      ),
      icon: <SearchIcon />,
      run: go(`/chat/${hit.conversation_id}?message=${hit.message_id}`),
    }));
    const searchAll: PaletteAction = {
      id: "search-all",
      section: "Messages",
      label: `Search all messages for “${query.trim()}”`,
      icon: <SearchIcon />,
      run: go(`/search?q=${encodeURIComponent(query.trim())}`),
    };
    return [...matching, ...messages, searchAll];
  }, [query, environments, active, conversations, hits, navigate, onClose, selectEnvironment]);

  useEffect(() => setActiveIndex(0), [query]);

  function handleKeyDown(event: KeyboardEvent) {
    if (event.key === "Escape") {
      onClose();
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((index) => Math.min(index + 1, actions.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((index) => Math.max(index - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      actions[activeIndex]?.run();
    }
  }

  return (
    <div className="palette-backdrop" onMouseDown={onClose}>
      <div
        className="palette"
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        onMouseDown={(event) => event.stopPropagation()}
        onKeyDown={handleKeyDown}
      >
        <div className="palette-input">
          <SearchIcon />
          <label htmlFor="palette-query" className="visually-hidden">
            Command or search
          </label>
          <input
            id="palette-query"
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Type a command or search conversations…"
            autoComplete="off"
          />
          <kbd>Esc</kbd>
        </div>
        {actions.length === 0 ? (
          <p className="palette-empty">Nothing matches “{query}”.</p>
        ) : (
          <ul className="palette-list">
            {actions.map((action, index) => (
              <li key={action.id}>
                {(index === 0 || actions[index - 1].section !== action.section) && (
                  <div className="palette-section">{action.section}</div>
                )}
                <button
                  type="button"
                  data-active={index === activeIndex}
                  onMouseEnter={() => setActiveIndex(index)}
                  onClick={action.run}
                >
                  {action.icon}
                  <span>{action.label}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
