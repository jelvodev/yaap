import { useEffect, useRef } from "react";
import { useParams, useSearchParams } from "react-router-dom";

export interface ChatLinkHandlers {
  onProject: (projectId: number) => void;
  /** Open the panel a `?panel=` value names (an old tab value such as `memory`, see `railPanels`). */
  onPanel: (value: string) => void;
  onAgent: (name: string) => void;
}

/** The conversation the address asks for, and the message a search result opens it at. */
export interface ChatAddress {
  selectedId: number | null;
  focusMessageId: number | null;
}

/** /chat/<id> opens a conversation; a search result adds ?message=<id> to open it at the message it found. */
export function useChatAddress(): ChatAddress {
  const { conversationId } = useParams();
  const [searchParams] = useSearchParams();
  return {
    selectedId: conversationId ? Number(conversationId) : null,
    focusMessageId: Number(searchParams.get("message")) || null,
  };
}

/**
 * Links into the chat: ?project=<id> opens that project, ?panel=<tab> its panel and ?agent=<name> a new conversation
 * as that agent. They are applied once and then removed from the address.
 */
export function useChatLinks(handlers: ChatLinkHandlers): void {
  const [searchParams, setSearchParams] = useSearchParams();
  const latest = useRef(handlers);
  latest.current = handlers;

  useEffect(() => {
    const project = Number(searchParams.get("project")) || null;
    const panel = searchParams.get("panel");
    const agent = searchParams.get("agent");
    if (project === null && panel === null && agent === null) return;
    if (project !== null) latest.current.onProject(project);
    if (panel) latest.current.onPanel(panel);
    if (agent) latest.current.onAgent(agent);
    const next = new URLSearchParams(searchParams);
    next.delete("project");
    next.delete("panel");
    next.delete("agent");
    setSearchParams(next, { replace: true });
  }, [searchParams]);
}
