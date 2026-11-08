import { api } from "./client";
import type { components } from "./schema";

/** A utility task that a check (usually the model policy) skipped, from its audit row; also a reply stream event. */
export type UtilitySkip = components["schemas"]["UtilitySkipOut"];

/** The skipped utility tasks of the conversation's last exchange; empty when they cannot be read. */
export async function conversationSkips(conversationId: number): Promise<UtilitySkip[]> {
  const { data } = await api.GET("/api/v1/conversations/{conversation_id}/utility-skips", {
    params: { path: { conversation_id: conversationId } },
  });
  return data ?? [];
}

/** The project's background tasks whose latest attempt was skipped; empty when they cannot be read. */
export async function projectSkips(projectId: number): Promise<UtilitySkip[]> {
  const { data } = await api.GET("/api/v1/projects/{project_id}/utility-skips", {
    params: { path: { project_id: projectId } },
  });
  return data ?? [];
}
