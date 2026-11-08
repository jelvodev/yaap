import { type Dispatch, type SetStateAction, useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { type Conversation, type ConversationSummary, type Message, api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";
import { stopReading } from "../../speech/readAloud";

export type AccessLevel = components["schemas"]["ConversationSummaryOut"]["access"] & string;
type ConversationUpdate = components["schemas"]["ConversationUpdate"];

/** What creating a conversation needs: the request, and the context window to show until the first reply. */
export interface NewConversation {
  body: components["schemas"]["ConversationCreate"];
  contextWindow: number | null;
}

export interface ActiveConversationOptions {
  environmentId: number;
  /** The conversation the address asks for. */
  selectedId: number | null;
  onSummary: (summary: ConversationSummary, moveToTop?: boolean) => void;
  onForget: (conversationId: number) => void;
  /** Opening a conversation shows its project in the sidebar. */
  onProjectShown: (projectId: number | null) => void;
}

export type ActiveConversation = ReturnType<typeof useActiveConversation>;

/** The open conversation, the notice shown under its messages, and the changes made to it from the page. */
export function useActiveConversation(options: ActiveConversationOptions) {
  const { environmentId, selectedId, onSummary, onForget, onProjectShown } = options;
  const navigate = useNavigate();
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Reading a reply aloud stops when its conversation is left.
  useEffect(() => stopReading, [conversation?.id]);

  const load = useCallback(
    async (id: number) => {
      const { data, error: requestError } = await api.GET("/api/v1/conversations/{conversation_id}", {
        params: { path: { conversation_id: id } },
      });
      if (requestError || !data) {
        setError(errorMessage(requestError, "The conversation could not be loaded."));
        return;
      }
      setConversation(data);
      onProjectShown(data.project_id ?? null);
    },
    [onProjectShown],
  );

  async function create({ body, contextWindow }: NewConversation): Promise<Conversation | null> {
    const { data, error: requestError } = await api.POST("/api/v1/environments/{environment_id}/conversations", {
      params: { path: { environment_id: environmentId } },
      body,
    });
    if (requestError || !data) {
      setError(errorMessage(requestError, "The conversation could not be created."));
      return null;
    }
    const created: Conversation = {
      ...data,
      environment_id: environmentId,
      messages: [],
      utility_events: [],
      context_tokens: null,
      context_window: contextWindow,
    };
    setConversation(created);
    onSummary(data);
    navigate(`/chat/${data.id}`, { replace: true });
    return created;
  }

  /** Rename or archive a conversation of the sidebar; returns the error to show there, or null. */
  async function update(id: number, body: { title?: string; archived?: boolean }): Promise<string | null> {
    const { data, error: requestError } = await api.PATCH("/api/v1/conversations/{conversation_id}", {
      params: { path: { conversation_id: id } },
      body,
    });
    if (requestError || !data) return errorMessage(requestError, "The conversation could not be changed.");
    onSummary(data, false);
    setConversation((current) =>
      current?.id === id ? { ...current, title: data.title, archived_at: data.archived_at } : current,
    );
    return null;
  }

  async function remove(id: number): Promise<string | null> {
    const { error: requestError } = await api.DELETE("/api/v1/conversations/{conversation_id}", {
      params: { path: { conversation_id: id } },
    });
    if (requestError) return errorMessage(requestError, "The conversation could not be deleted.");
    onForget(id);
    if (selectedId === id) {
      setConversation(null);
      navigate("/chat");
    }
    return null;
  }

  return {
    conversation,
    error,
    setError,
    load,
    create,
    update,
    remove,
    clear: () => setConversation(null),
    ...settingChanges({ conversation, setConversation, setError, onSummary }),
    ...messageEdits(setConversation),
  };
}

type SetConversation = Dispatch<SetStateAction<Conversation | null>>;

interface SettingContext {
  conversation: Conversation | null;
  setConversation: SetConversation;
  setError: (message: string) => void;
  onSummary: ActiveConversationOptions["onSummary"];
}

/** Changes to the settings of the open conversation: access, agent, pinned skills and the version shown. */
function settingChanges({ conversation, setConversation, setError, onSummary }: SettingContext) {
  /** Change a setting of the open conversation and keep the stored value. */
  async function patch(
    body: ConversationUpdate,
    failure: string,
    apply: (summary: ConversationSummary) => Partial<Conversation>,
  ): Promise<void> {
    if (!conversation) return;
    const { data, error: requestError } = await api.PATCH("/api/v1/conversations/{conversation_id}", {
      params: { path: { conversation_id: conversation.id } },
      body,
    });
    if (requestError || !data) {
      setError(errorMessage(requestError, failure));
      return;
    }
    setConversation((current) => (current?.id === data.id ? { ...current, ...apply(data) } : current));
    onSummary(data, false);
  }

  /** Change what the conversation may do; a running reply gets it from its next tool call on. */
  const changeAccess = (access: AccessLevel) =>
    patch({ access }, "The access could not be changed.", (data) => ({ access: data.access }));
  const changeAgent = (name: string) =>
    patch({ agent: name || null }, "The agent could not be changed.", (data) => ({ agent: data.agent }));

  async function changePinnedSkills(skills: string[]) {
    if (!conversation) return;
    const { data, error: requestError } = await api.PUT("/api/v1/conversations/{conversation_id}/pinned-skills", {
      params: { path: { conversation_id: conversation.id } },
      body: { skills },
    });
    if (requestError || !data) {
      setError(errorMessage(requestError, "The skills could not be pinned."));
      return;
    }
    setConversation((current) => (current ? { ...current, pinned_skills: data.pinned_skills } : current));
  }

  /** Show another version of a message (branch navigation). */
  async function showVersion(messageId: number) {
    if (!conversation) return;
    const { data, error: switchError } = await api.PUT("/api/v1/conversations/{conversation_id}/head", {
      params: { path: { conversation_id: conversation.id } },
      body: { message_id: messageId },
    });
    if (switchError || !data) setError(errorMessage(switchError, "That version could not be shown."));
    else setConversation(data);
  }

  return { changeAccess, changeAgent, changePinnedSkills, showVersion };
}

/** Changes to the shown messages while a reply streams, ahead of the stored conversation. */
function messageEdits(setConversation: SetConversation) {
  function appendMessage(message: Message) {
    setConversation((current) => {
      if (!current) return current;
      // The copy shown at sending (a negative id) makes way for the stored message.
      const kept = message.role === "user" ? current.messages.filter((item) => item.id > 0) : current.messages;
      // A followed reply starts again from its first event; a message that is already shown stays once.
      if (kept.some((item) => item.id === message.id)) {
        return kept.length === current.messages.length ? current : { ...current, messages: kept };
      }
      return { ...current, messages: [...kept, message] };
    });
  }

  /** Show a sent message at once, before the server has stored it; the stored one replaces it. */
  function showSentMessage(conversationId: number, text: string) {
    if (!text.trim()) return;
    const shown: Message = {
      id: -Date.now(),
      role: "user",
      content: [{ type: "text", text: text.trim(), is_error: false }],
      created_at: new Date().toISOString(),
      error: null,
      parent_id: null,
      status: "complete",
      stop_reason: null,
    };
    setConversation((current) =>
      current && current.id === conversationId ? { ...current, messages: [...current.messages, shown] } : current,
    );
  }

  /** Show the conversation up to (not including) a message, while its new version streams in. */
  function cutBefore(messageId: number) {
    setConversation((current) => {
      if (!current) return current;
      const index = current.messages.findIndex((item) => item.id === messageId);
      return index < 0 ? current : { ...current, messages: current.messages.slice(0, index) };
    });
  }

  /** A better title arrived for a conversation. */
  function applyTitle(summary: ConversationSummary) {
    setConversation((current) => (current?.id === summary.id ? { ...current, title: summary.title } : current));
  }

  /** The reply ended: the conversation's title and archived state as the server has them. */
  function applySummary(summary: ConversationSummary) {
    setConversation((current) =>
      current ? { ...current, title: summary.title, archived_at: summary.archived_at } : current,
    );
  }

  return { appendMessage, showSentMessage, cutBefore, applyTitle, applySummary };
}
