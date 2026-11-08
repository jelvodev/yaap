/**
 * What the Changes panel offers for a repository's state: which buttons show, the Sync label and the short sync
 * text. Pure functions, so `changesState.test.mjs` can check every state.
 */

/** The facts of a repository the panel decides on; `GitRepository` fits it. */
export interface RepositoryFacts {
  changes: readonly unknown[];
  ahead: number;
  behind: number;
  upstream?: string | null;
  remote_url?: string | null;
  has_commits: boolean;
}

/** How the Sync button shows: not at all (no remote), beside Commit, or as the one thing to do. */
export type SyncEmphasis = "hidden" | "secondary" | "primary";

export interface ChangesActions {
  /** The commit message field, Suggest and *Commit N files*. */
  commit: boolean;
  /** *Review these changes*: a review of the uncommitted changes. */
  review: boolean;
  /** *Discard all* and the discard button of each changed file. */
  discard: boolean;
  sync: SyncEmphasis;
  /** The items of Sync's menu. */
  fetch: boolean;
  pull: boolean;
  push: boolean;
  /** No remote is set: point to Project settings, where it is set. */
  remoteHint: boolean;
}

/** A push would send something: commits ahead, or a branch with commits that was never pushed. */
function somethingToPush(repository: RepositoryFacts): boolean {
  return repository.ahead > 0 || (!repository.upstream && repository.has_commits);
}

/** Which of the panel's actions show for this state. */
export function changesActions(repository: RepositoryFacts): ChangesActions {
  const changed = repository.changes.length > 0;
  const hasRemote = Boolean(repository.remote_url);
  const outOfStep = somethingToPush(repository) || repository.behind > 0;
  return {
    commit: changed,
    review: changed,
    discard: changed,
    sync: !hasRemote ? "hidden" : !changed && outOfStep ? "primary" : "secondary",
    fetch: hasRemote,
    pull: hasRemote && Boolean(repository.upstream),
    push: hasRemote && repository.has_commits,
    remoteHint: !hasRemote,
  };
}

/** The Sync button's label: `Sync · ↑1 · ↓0`, or what keeps it from counting. */
export function syncLabel(repository: RepositoryFacts): string {
  if (!repository.remote_url) return "No remote";
  if (!repository.upstream) return repository.has_commits ? "Sync · not pushed yet" : "Sync";
  return `Sync · ↑${repository.ahead} · ↓${repository.behind}`;
}

/** Where the branch stands against its remote, in words, for the line beside the branch name. */
export function syncText(repository: RepositoryFacts): string {
  if (!repository.remote_url) return "no remote";
  if (!repository.upstream) return "not pushed yet";
  const parts = [];
  if (repository.ahead) parts.push(`${repository.ahead} to push`);
  if (repository.behind) parts.push(`${repository.behind} to pull`);
  return parts.length ? parts.join(" · ") : "up to date";
}

const STATE_LETTERS: Record<string, string> = {
  added: "A",
  untracked: "A",
  modified: "M",
  deleted: "D",
  renamed: "R",
  conflict: "C",
};

/** The one-letter status of a changed file, as git shows it. */
export function stateLetter(state: string): string {
  return STATE_LETTERS[state] ?? "?";
}

/** `Commit 1 file`, `Commit 5 files`. */
export function commitLabel(count: number): string {
  return `Commit ${count} file${count === 1 ? "" : "s"}`;
}

/** The change of one file the discard question names; `GitRepository["changes"][number]` fits it. */
export interface ChangeFacts {
  path: string;
  state: string;
}

/**
 * The confirmation question before a discard: what is lost, for one file or for all of them. A checkpoint is taken
 * first, and rolling back to it is the only way to get the changes back.
 */
export function discardQuestion(target: ChangeFacts | { all: number }): string {
  let question: string;
  if ("all" in target) {
    const files = `${target.all} changed file${target.all === 1 ? "" : "s"}`;
    question = `Discard the changes to all ${files}? Edits are lost and new files are deleted.`;
  } else if (target.state === "untracked" || target.state === "added") {
    question = `Delete the new file ${target.path}?`;
  } else {
    question = `Discard the changes to ${target.path}? It goes back to the last commit.`;
  }
  return `${question} This cannot be undone, except by rolling back to the checkpoint taken first (Checkpoints tab).`;
}
