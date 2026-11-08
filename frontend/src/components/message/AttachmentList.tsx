import { useState } from "react";

import type { Message } from "../../api/client";
import { attachmentContentUrl, formatSize, kindLabel } from "../../api/attachments";
import { ArchiveIcon, FileIcon, FolderIcon } from "../icons";

type ContentBlock = Message["content"][number];

/** The files, folders and images a user message carries: images as thumbnails that enlarge, the rest as downloads. */
export default function AttachmentList({ blocks }: { blocks: ContentBlock[] }) {
  const [enlarged, setEnlarged] = useState<ContentBlock | null>(null);
  return (
    <div className="message-attachments">
      {blocks.map((block) => {
        const url = attachmentContentUrl(block.attachment_id ?? 0);
        if (block.kind === "image") {
          return (
            <button
              key={block.attachment_id}
              type="button"
              className="attachment-thumb"
              onClick={() => setEnlarged(block)}
              aria-label={`Show ${block.name}`}
            >
              <img src={url} alt={block.name ?? ""} loading="lazy" />
            </button>
          );
        }
        return (
          <a
            key={block.attachment_id}
            className="attachment-chip"
            href={url}
            download={block.kind === "folder" ? `${block.name}.zip` : (block.name ?? undefined)}
          >
            {block.kind === "folder" ? <FolderIcon size={16} /> : block.kind === "archive" ? <ArchiveIcon size={16} /> : <FileIcon size={16} />}
            <span className="attachment-chip-text">
              <span className="attachment-chip-name">{block.name}</span>
              <span className="attachment-chip-meta">
                {kindLabel(block.kind)} · {formatSize(block.size ?? 0)}
              </span>
            </span>
          </a>
        );
      })}
      {enlarged && (
        <div className="lightbox" role="dialog" aria-label={enlarged.name ?? "Image"} onClick={() => setEnlarged(null)}>
          <img src={attachmentContentUrl(enlarged.attachment_id ?? 0)} alt={enlarged.name ?? ""} />
          <span className="lightbox-caption">{enlarged.name}</span>
        </div>
      )}
    </div>
  );
}
