import { useMemo, useState } from "react";

import type { GitRepository } from "../../api/client";
import DiffView from "../DiffView";
import { ChevronIcon } from "../icons";
import { stateLetter } from "./changesState";
import { diffBody, diffPreview, diffStats } from "./diffParts";

type FileChange = GitRepository["changes"][number];

/** How many lines of a file's diff show before "… N more lines". */
const PREVIEW_LINES = 30;

/** One changed file: its status, path and line counts; a click opens its diff in place. */
export default function ChangedFileRow({
  change,
  part,
  open,
  onToggle,
}: {
  change: FileChange;
  /** The file's part of the uncommitted diff; empty when the diff has none for it. */
  part: string;
  open: boolean;
  onToggle: () => void;
}) {
  const [whole, setWhole] = useState(false);
  const stats = useMemo(() => diffStats(part), [part]);
  const body = useMemo(() => diffBody(part), [part]);
  const preview = whole ? { shown: body, more: 0 } : diffPreview(body, PREVIEW_LINES);
  const diffId = `changes-diff-${change.path}`;

  return (
    <li className="cp-file">
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
