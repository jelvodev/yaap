import { useCallback } from "react";
import { useNavigate } from "react-router-dom";

/**
 * Open a new conversation in the project, as the sidebar's New conversation does with the project chosen: the
 * composer opens with the project set and nothing is sent.
 */
export function useNewConversation(projectId: number, onScope: (projectId: number | null) => void): () => void {
  const navigate = useNavigate();
  return useCallback(() => {
    onScope(projectId);
    navigate("/chat");
  }, [navigate, onScope, projectId]);
}
