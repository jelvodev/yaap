import { useCallback, useEffect, useRef } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

export interface ChatLinkHandlers {
  onProject: (projectId: number) => void;
  /** Open the panel a `?panel=` value names (an old tab value such as `memory`, see `railPanels`). */
  onPanel: (value: string) => void;
  onAgent: (name: string) => void;
}

/** The conversation the address asks for, the message a search result opens it at, or a project's board and card. */
export interface ChatAddress {
  selectedId: number | null;
  focusMessageId: number | null;
  /** /chat/projects/<id> shows that project's board in place of the conversation. */
  boardProjectId: number | null;
  /** /chat/projects/<id>/cards/<card> opens a card beside the board. */
  cardId: number | null;
}

/** A positive whole number from an address part, or null. */
export function addressNumber(value: string | null | undefined): number | null {
  if (!value || !/^\d+$/.test(value)) return null;
  const number = Number(value);
  return Number.isSafeInteger(number) && number > 0 ? number : null;
}

/** /chat/<id> opens a conversation; a search result adds ?message=<id> to open it at the message it found. */
export function useChatAddress(): ChatAddress {
  const { conversationId, projectId, cardId } = useParams();
  const [searchParams] = useSearchParams();
  const boardProjectId = addressNumber(projectId);
  return {
    selectedId: conversationId ? Number(conversationId) : null,
    focusMessageId: Number(searchParams.get("message")) || null,
    boardProjectId,
    cardId: boardProjectId === null ? null : addressNumber(cardId),
  };
}

export function boardPath(projectId: number): string {
  return `/chat/projects/${projectId}`;
}

export function cardPath(projectId: number, cardId: number): string {
  return `/chat/projects/${projectId}/cards/${cardId}`;
}

/** Going to a project's board or one of its cards. Only numbers go into the address, never card text. */
export function useBoardLinks(): {
  openBoard: (projectId: number) => void;
  openCard: (projectId: number, cardId: number) => void;
} {
  const navigate = useNavigate();
  const openBoard = useCallback((projectId: number) => navigate(boardPath(projectId)), [navigate]);
  const openCard = useCallback(
    (projectId: number, cardId: number) => navigate(cardPath(projectId, cardId)),
    [navigate],
  );
  return { openBoard, openCard };
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
