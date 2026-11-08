import { type components } from "./schema";
import { errorMessage } from "./client";
import { isFolderZip } from "./folders";

export type Attachment = components["schemas"]["AttachmentOut"];

/** An attachment in the composer, from the moment it is added until the message is sent. */
export interface PendingAttachment {
  localId: string;
  name: string;
  size: number;
  /** Local preview for images, released when the attachment leaves the composer. */
  previewUrl: string | null;
  status: "uploading" | "ready" | "error";
  attachment: Attachment | null;
  error: string | null;
}

export function attachmentContentUrl(attachmentId: number): string {
  return `/api/v1/attachments/${attachmentId}/content`;
}

export async function uploadAttachment(conversationId: number, file: File): Promise<Attachment> {
  const form = new FormData();
  form.append("file", file, file.name || "pasted-image.png");
  // A folder packed in the browser becomes one attachment of kind folder.
  if (isFolderZip(file)) form.append("kind", "folder");
  const response = await fetch(`/api/v1/conversations/${conversationId}/attachments`, { method: "POST", body: form });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(errorMessage(body, `${file.name} could not be added (HTTP ${response.status}).`));
  return body as Attachment;
}

export async function removeAttachment(attachmentId: number): Promise<void> {
  await fetch(`/api/v1/attachments/${attachmentId}`, { method: "DELETE" });
}

export function formatSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(0)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

const KIND_LABELS: Record<string, string> = {
  image: "Image",
  pdf: "PDF",
  text: "Text",
  office: "Office",
  archive: "Zip",
  folder: "Folder",
  other: "File",
};

export function kindLabel(kind: string | null | undefined): string {
  return KIND_LABELS[kind ?? "other"] ?? "File";
}
