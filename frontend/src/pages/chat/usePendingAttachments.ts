import { useEffect, useState } from "react";

import { type PendingAttachment, removeAttachment, uploadAttachment } from "../../api/attachments";
import { errorMessage } from "../../api/client";

/** An attachment in the composer, tied to the conversation it was uploaded to. */
export type ComposerAttachment = PendingAttachment & { conversationId: number };

export interface PendingAttachments {
  /** The attachments of the open conversation. */
  here: ComposerAttachment[];
  add: (conversationId: number, files: File[]) => Promise<void>;
  remove: (localId: string) => void;
  /** The uploaded attachments of a conversation, ready to go with a message. */
  ready: (conversationId: number) => ComposerAttachment[];
  /** Take attachments out of the composer once a message or the queue owns them. */
  release: (items: ComposerAttachment[]) => void;
}

/** The ids of uploaded attachments, to send with a message. */
export function attachmentIds(items: ComposerAttachment[]): number[] {
  return items.map((item) => item.attachment?.id).filter((id): id is number => id !== undefined);
}

/** Attachments in the composer, from the moment they are added until the message is sent. */
export function usePendingAttachments(conversationId: number | null): PendingAttachments {
  const [pending, setPending] = useState<ComposerAttachment[]>([]);

  // Attachments uploaded to a conversation the user left are removed, so nothing unsent lingers.
  useEffect(() => {
    setPending((current) => {
      const leaving = current.filter((item) => item.conversationId !== conversationId);
      for (const item of leaving) {
        if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
        if (item.attachment) void removeAttachment(item.attachment.id);
      }
      return leaving.length ? current.filter((item) => !leaving.includes(item)) : current;
    });
  }, [conversationId]);

  function updatePending(localId: string, changes: Partial<PendingAttachment>) {
    setPending((current) => current.map((item) => (item.localId === localId ? { ...item, ...changes } : item)));
  }

  async function add(id: number, files: File[]) {
    const items = files.map((file) => ({
      conversationId: id,
      localId: crypto.randomUUID(),
      name: file.name || "pasted-image.png",
      size: file.size,
      previewUrl: file.type.startsWith("image/") ? URL.createObjectURL(file) : null,
      status: "uploading" as const,
      attachment: null,
      error: null,
    }));
    setPending((current) => [...current, ...items]);
    await Promise.all(
      items.map(async (item, index) => {
        try {
          const attachment = await uploadAttachment(id, files[index]);
          updatePending(item.localId, { status: "ready", attachment });
        } catch (uploadError) {
          updatePending(item.localId, { status: "error", error: errorMessage(uploadError) });
        }
      }),
    );
  }

  function remove(localId: string) {
    setPending((current) => {
      const item = current.find((entry) => entry.localId === localId);
      if (item?.previewUrl) URL.revokeObjectURL(item.previewUrl);
      if (item?.attachment) void removeAttachment(item.attachment.id);
      return current.filter((entry) => entry.localId !== localId);
    });
  }

  function release(items: ComposerAttachment[]) {
    setPending((current) => current.filter((item) => !items.includes(item)));
    for (const item of items) if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
  }

  return {
    here: pending.filter((item) => item.conversationId === conversationId),
    add,
    remove,
    ready: (id) => pending.filter((item) => item.conversationId === id && item.status === "ready"),
    release,
  };
}
