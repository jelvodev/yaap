import { useMemo, useState } from "react";

import type { GitRepository } from "../../api/client";
import DiffView from "../DiffView";
import { ChevronIcon, UndoIcon } from "../icons";
import { discardQuestion, stateLetter } from "./changesState";
import DiscardConfirm from "./DiscardConfirm";
import { diffBody, diffPreview, diffStats } from "./diffParts";

type FileChange = GitRepository["changes"][number];

/** How many lines of a file's diff show before "… N more lines". */
const PREVIEW_LINES = 30;

/**
 * One changed file: its status, path and line counts; a click opens its diff in place. On hover a button discards
 * the file's changes, after a confirmation.
 */
export default function ChangedFileRow({
  change,
  part,
  open,
  disabled,
  discarding,
  onToggle,
  onDiscard,
}: {
  change: FileChange;
  /** The file's part of the uncommitted diff; empty when the diff has none for it. */
  part: string;
  open: boolean;
  /** A git action runs, so no discard can start. */
  disabled: boolean;
  /** A discard runs. */
  discarding: boolean;
  onToggle: () => void;
  /** Discard this file's changes; true when it worked. */
  onDiscard: () => Promise<boolean>;
}) {
  const [whole, setWhole] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const stats = useMemo(() => diffStats(part), [part]);
  const body = useMemo(() => diffBody(part), [part]);
  const preview = whole ? { shown: body, more: 0 } : diffPreview(body, PREVIEW_LINES);
  const diffId = `changes-diff-${change.path}`;

  return (
    <li className="cp-file">
      <div className="cp-file-line">
        <button
          type="button"
          className="cp-file-head"
          aria-expanded={open}
          aria-controls={open ? diffId : undefined}
          title={change.staged ? `${change.state} · staged` : change.state}
          onClick={onToggle}
        >
          <span className="cp-chevron" data-open={open}>
            <ChevronIcon size={13} />
          </span>
          <span className={`pp-state change-${change.state}`}>{stateLetter(change.state)}</span>
          <span className="pp-file-path mono">{change.path}</span>
          <span className="cp-stats mono">
            {stats.added > 0 && <span className="diff-add">+{stats.added}</span>}
            {stats.removed > 0 && <span className="diff-remove">−{stats.removed}</span>}
          </span>
        </button>
        <button
          type="button"
          className="icon-button cp-file-discard"
          disabled={disabled}
          aria-label={`Discard the changes to ${change.path}`}
          title="Discard this file's changes"
          onClick={() => setConfirming(true)}
        >
          <UndoIcon size={14} />
        </button>
      </div>
      {confirming && (
        <DiscardConfirm
          question={discardQuestion(change)}
          busy={discarding}
          onCancel={() => setConfirming(false)}
          onDiscard={() => void onDiscard().then(() => setConfirming(false))}
        />
      )}
      {open && (
        <div className="cp-file-diff" id={diffId}>
          {part ? (
            <DiffView diff={preview.shown} />
          ) : (
            <p className="faint small cp-file-empty">No diff to show for this file.</p>
          )}
          {preview.more > 0 && (
            <button type="button" className="link-button small cp-more" onClick={() => setWhole(true)}>
              … {preview.more} more line{preview.more === 1 ? "" : "s"}
            </button>
          )}
        </div>
      )}
    </li>
  );
}
