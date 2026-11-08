import { api, errorMessage } from "../../api/client";

/** A screenshot of the agent's browser in a conversation, as the server keeps it. */
export function browserShotUrl(conversationId: number, shot: number): string {
  return `/api/v1/conversations/${conversationId}/browser/shots/${shot}`;
}

/**
 * A screenshot of the running preview as a file to attach: the preview panel's screenshot through the
 * conversation's browser, then the kept image itself. Throws with a message the composer can show.
 */
export async function previewScreenshotFile(conversationId: number, processId: number): Promise<File> {
  const { data, error } = await api.POST("/api/v1/conversations/{conversation_id}/preview/screenshot", {
    params: { path: { conversation_id: conversationId } },
    body: { process_id: processId, path: "/" },
  });
  if (!data) throw new Error(errorMessage(error, "The screenshot could not be taken."));
  const response = await fetch(browserShotUrl(conversationId, data.number));
  if (!response.ok) throw new Error("The screenshot was taken but could not be read.");
  const image = await response.blob();
  const extension = image.type === "image/jpeg" ? "jpg" : "png";
  return new File([image], `preview-${data.number}.${extension}`, { type: image.type || "image/png" });
}
