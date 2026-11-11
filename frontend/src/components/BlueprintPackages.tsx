import { useEffect, useState } from "react";

import { api, errorMessage, type MutationStatus } from "../api/client";
import type { components } from "../api/schema";
import DiffView from "./DiffView";
import MarkedText from "./MarkedText";
import {
  type ItemChoice,
  type LevelQuery,
  type PackageItem,
  type PackageReview,
  choiceKey,
  targetFor,
  usePackageReviews,
} from "./usePackageReviews";

type Installed = components["schemas"]["InstalledPackageOut"];
type PackageOrigin = components["schemas"]["PackageOriginOut"];
type UpdateCheck = components["schemas"]["PackageUpdateOut"];

/** A level the user can install into: global, the environment, or the project. */
export interface PackageLevel {
  key: string;
  name: string;
  query: LevelQuery;
}

const KIND_LABELS: Record<PackageItem["kind"], string> = {
  standard: "Standard",
  engineering: "Engineering",
  skill: "Skill",
  command: "Command",
  agent: "Agent",
};

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

/** One package item to review: kind, name, description, warnings or why it is refused, its text and files. */
export function PackageItemReview({
  item,
  chosen,
  name,
  onChoose,
  onRename,
}: {
  item: PackageItem;
  chosen: boolean;
  name: string;
  onChoose?: (chosen: boolean) => void;
  onRename?: (name: string) => void;
}) {
  const [show, setShow] = useState<"text" | "files" | null>(null);
  const main = item.kind === "skill" ? "SKILL.md" : item.path.split("/").pop();
  return (
    <li className={chosen ? "import-item" : "import-item import-off"}>
      <div className="form-row">
        {onChoose && (
          <input
            type="checkbox"
            checked={chosen}
            disabled={Boolean(item.error)}
            aria-label={`Install ${item.name}`}
            onChange={(event) => onChoose(event.target.checked)}
          />
        )}
        <span className="skill-badge">{KIND_LABELS[item.kind]}</span>
        {onRename ? (
          <input value={name} aria-label={`Name of ${item.path}`} onChange={(event) => onRename(event.target.value.toLowerCase())} />
        ) : (
          <strong>{name}</strong>
        )}
        <span className="mono small faint" title={`From ${item.path}`}>
          → {targetFor(item.kind, name)}
        </span>
      </div>
      <p className="small">{item.description || <span className="faint">No description.</span>}</p>
      {item.error && <p className="form-error">{item.error}</p>}
      {item.warnings.length > 0 && (
        <ul className="skill-warnings">
          {item.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}
      <p className="faint small">
        <span className="mono">{item.path}</span> · {item.files.length} file{item.files.length === 1 ? "" : "s"} ·{" "}
        {formatSize(item.size)}
        {item.text && (
          <>
            {" · "}
            <button type="button" className="link-button" onClick={() => setShow(show === "text" ? null : "text")}>
              {show === "text" ? "Hide text" : `Show ${main}`}
            </button>
          </>
        )}
        {item.kind === "skill" && (
          <>
            {" · "}
            <button type="button" className="link-button" onClick={() => setShow(show === "files" ? null : "files")}>
              {show === "files" ? "Hide files" : "Show files"}
            </button>
          </>
        )}
      </p>
      {show === "text" && (
        <pre className="import-preview">
          <MarkedText text={item.text_marked} />
        </pre>
      )}
      {show === "files" && (
        <pre className="import-preview">{item.files.map((file) => `${file.path}  (${formatSize(file.size)})`).join("\n")}</pre>
      )}
    </li>
  );
}

/** The packages installed at one level, with their items. */
function InstalledPackages({ level }: { level: PackageLevel }) {
  const [installed, setInstalled] = useState<Installed[] | null>(null);
  const [version, setVersion] = useState(0);
  const [checking, setChecking] = useState<string | null>(null);
  const query = level.query;

  useEffect(() => {
    let current = true;
    void api.GET("/api/v1/blueprint/packages", { params: { query } }).then(({ data }) => {
      if (current) setInstalled(data ?? []);
    });
    return () => {
      current = false;
    };
    // The level's key stands for its query, which is a new object on every render of the page.
  }, [level.key, version]);

  if (!installed || installed.length === 0) return null;
  return (
    <>
      <h4>Installed in {level.name}</h4>
      <ul className="import-list">
        {installed.map((entry) => (
          <li key={entry.name} className="import-item">
            <div className="form-row">
              <strong>{entry.name}</strong>
              <a href={entry.url} target="_blank" rel="noreferrer" className="small">
                {entry.repository}
              </a>
              <span className="faint small">{entry.ref}</span>
              {checking !== entry.name && (
                <button type="button" className="link-button small" onClick={() => setChecking(entry.name)}>
                  Check for updates
                </button>
              )}
            </div>
            {entry.description && <p className="small">{entry.description}</p>}
            <p className="faint small">
              {entry.items.map((item) => (
                <span key={item.path} className="mono" style={{ marginRight: "0.75em" }}>
                  {item.path} @{item.commit.slice(0, 7)}
                  {item.changed_locally ? " (changed locally)" : ""}
                </span>
              ))}
            </p>
            {checking === entry.name && (
              <PackageUpdateReview
                name={entry.name}
                query={query}
                onClose={() => setChecking(null)}
                onUpdated={() => {
                  setChecking(null);
                  setVersion((value) => value + 1);
                }}
              />
            )}
          </li>
        ))}
      </ul>
    </>
  );
}

/** A package read for review: what it is, where it was read, and every item with its choice. */
export function PackageReviewList({
  review,
  onChoose,
}: {
  review: PackageReview;
  onChoose: (url: string, key: string, change: Partial<ItemChoice>) => void;
}) {
  const { preview } = review;
  return (
    <>
      <p className="small">
        <strong>{preview.package.name}</strong> {preview.package.description}
      </p>
      <p className="faint small">
        {preview.source.repository} · {preview.source.ref} ·{" "}
        <a href={preview.source.url} target="_blank" rel="noreferrer" className="mono">
          {preview.source.commit.slice(0, 7)}
        </a>
      </p>
      <ul className="import-list">
        {preview.items.map((item) => {
          const key = choiceKey(item);
          return (
            <PackageItemReview
              key={key}
              item={item}
              chosen={!item.error && (review.choices[key]?.chosen ?? false)}
              name={review.choices[key]?.name ?? item.name}
              onChoose={(value) => onChoose(review.url, key, { chosen: value })}
              onRename={(value) => onChoose(review.url, key, { name: value })}
            />
          );
        })}
      </ul>
    </>
  );
}

/** Add items from a Blueprint package on GitHub into a level the user picks, after reviewing them. */
export function PackageInstallPanel({
  levels,
  initialLevel,
  onClose,
  onInstalled,
}: {
  levels: PackageLevel[];
  initialLevel: string;
  onClose: () => void;
  onInstalled: (paths: string[], levelName: string) => void;
}) {
  const [levelKey, setLevelKey] = useState(initialLevel);
  const [url, setUrl] = useState("");
  const review = usePackageReviews();
  const level = levels.find((entry) => entry.key === levelKey) ?? levels[levels.length - 1];
  const current = review.reviews[0];

  async function install() {
    const { paths, complete } = await review.install(level.query);
    if (complete) onInstalled(paths, level.name);
  }

  return (
    <section className="card import-panel">
      <div className="card-header">
        <h3>Add from a package</h3>
        <button type="button" className="button button-ghost" onClick={onClose}>
          Close
        </button>
      </div>
      <p className="muted small">
        Paste a link to a GitHub repository or folder with a <span className="mono">blueprint-package.json</span>.
        YAAP reads it at one fixed commit and shows every item for review; nothing is written before you confirm.
        Packages hold text only: items with scripts or binary files are refused.
      </p>
      <div className="form-row">
        <label htmlFor="package-level" className="small">
          Install into
        </label>
        <select
          id="package-level"
          value={level.key}
          onChange={(event) => {
            setLevelKey(event.target.value);
            const next = levels.find((entry) => entry.key === event.target.value);
            if (current && next) void review.read([current.url], next.query, false);
          }}
        >
          {levels.map((entry) => (
            <option key={entry.key} value={entry.key}>
              {entry.name}
            </option>
          ))}
        </select>
      </div>
      <form
        className="form-row"
        onSubmit={(event) => {
          event.preventDefault();
          if (url.trim()) void review.read([url.trim()], level.query, true);
        }}
      >
        <label className="visually-hidden" htmlFor="package-url">
          Package link
        </label>
        <input
          id="package-url"
          className="grow"
          value={url}
          placeholder="https://github.com/owner/repository"
          onChange={(event) => setUrl(event.target.value)}
        />
        <button type="submit" className="button" disabled={review.busy !== null || !url.trim()}>
          {review.busy === "reading" ? "Reading…" : "Read"}
        </button>
      </form>
      {review.error && <p className="form-error">{review.error}</p>}
      {current && (
        <>
          <PackageReviewList review={current} onChoose={review.choose} />
          {review.problem && <p className="form-error">{review.problem}</p>}
          <div className="form-row">
            <button
              type="button"
              className="button button-primary"
              disabled={review.busy !== null || review.chosenCount === 0 || review.problem !== null}
              onClick={() => void install()}
            >
              {review.busy === "installing"
                ? "Installing…"
                : `Install ${review.chosenCount} item${review.chosenCount === 1 ? "" : "s"} in ${level.name}`}
            </button>
          </div>
        </>
      )}
      <InstalledPackages level={level} />
    </section>
  );
}

/**
 * Check a package for updates: every installed item with its upstream diff and local changes. Nothing is written
 * until the user accepts the items they choose; accepting writes exactly the reviewed commit.
 */
export function PackageUpdateReview({
  name,
  query,
  updateRef,
  focus,
  onUpdated,
  onClose,
}: {
  name: string;
  query: LevelQuery;
  // Another ref than the recorded one to move to, such as the starter's tag of a newer YAAP version.
  updateRef?: string;
  // The item the check was started from; it is listed first.
  focus?: string;
  onUpdated: (paths: string[]) => void | Promise<void>;
  onClose: () => void;
}) {
  const [check, setCheck] = useState<UpdateCheck | null>(null);
  const [accepted, setAccepted] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState<"checking" | "updating" | null>("checking");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    void api
      .GET("/api/v1/blueprint/packages/{name}/update", {
        params: { path: { name }, query: { ...query, ref: updateRef } },
      })
      .then(({ data, error: failure }) => {
        if (!current) return;
        setBusy(null);
        if (!data) {
          setError(errorMessage(failure, "Checking for updates failed."));
          return;
        }
        setCheck(data);
        setAccepted(
          Object.fromEntries(data.items.filter((item) => item.status === "changed" && !item.item?.error).map((item) => [item.path, true])),
        );
      });
    return () => {
      current = false;
    };
    // Checked once per opening; the query object is new on every render of the page.
  }, [name]);

  const chosen = check ? check.items.filter((item) => accepted[item.path]).map((item) => item.path) : [];

  async function accept() {
    if (!check) return;
    setBusy("updating");
    setError(null);
    const { data, error: failure } = await api.POST("/api/v1/blueprint/packages/{name}/update", {
      params: { path: { name }, query },
      body: { commit: check.latest_commit, paths: chosen, ref: updateRef },
    });
    setBusy(null);
    if (!data) {
      setError(errorMessage(failure, "The update could not be installed."));
      return;
    }
    if (!data.commit.committed) {
      setError(commitFailure(data.commit));
      return;
    }
    await onUpdated(data.items);
  }

  const items = check
    ? [...check.items].sort((a, b) => Number(b.path === focus) - Number(a.path === focus))
    : [];
  const changed = items.filter((item) => item.status === "changed");

  return (
    <div className="skill-origin">
      <div className="form-row">
        <strong className="small">Updates for {name}</strong>
        <button type="button" className="link-button small" onClick={onClose}>
          Close
        </button>
      </div>
      {busy === "checking" && <p className="muted small">Checking…</p>}
      {error && <p className="form-error">{error}</p>}
      {check && (
        <p className="muted small">
          Newest on {check.ref}:{" "}
          <a href={check.source.url} target="_blank" rel="noreferrer" className="mono">
            {check.latest_commit.slice(0, 7)}
          </a>
          . {changed.length ? `${changed.length} item${changed.length === 1 ? "" : "s"} changed.` : "Every item is up to date."}{" "}
          Nothing changes until you accept.
        </p>
      )}
      {items.map((item) => (
        <div key={item.path} className="import-item">
          <div className="form-row">
            {item.status === "changed" && (
              <input
                type="checkbox"
                checked={Boolean(accepted[item.path])}
                disabled={Boolean(item.item?.error)}
                aria-label={`Accept the update of ${item.path}`}
                onChange={(event) => setAccepted((current) => ({ ...current, [item.path]: event.target.checked }))}
              />
            )}
            <span className="mono small">{item.path}</span>
            <span className="faint small">
              {item.status === "changed"
                ? `${item.installed_commit.slice(0, 7)} → ${check?.latest_commit.slice(0, 7)}`
                : item.status === "removed"
                  ? "no longer in the package; it stays here until you delete it"
                  : "up to date"}
            </span>
            {item.changed_locally && <span className="skill-badge skill-badge-changed">Changed locally</span>}
          </div>
          {item.status === "changed" && item.upstream_diff && <DiffView diff={item.upstream_diff} />}
          {item.item && (item.item.error || item.item.warnings.length > 0) && (
            <ul className="import-list">
              <PackageItemReview item={item.item} chosen name={item.name} />
            </ul>
          )}
          {item.changed_locally && (
            <>
              <p className="muted small">
                Your changes since the install{item.status === "changed" ? "; accepting replaces them with the new version" : ""}:
              </p>
              <DiffView diff={item.local_diff} />
            </>
          )}
        </div>
      ))}
      {check && check.new_items.length > 0 && (
        <p className="muted small">
          New in the package: {check.new_items.map((item) => `${item.name} (${item.kind})`).join(", ")}. Add them with Add from a
          package.
        </p>
      )}
      {changed.length > 0 && (
        <div className="form-row">
          <button
            type="button"
            className="button button-primary"
            disabled={busy !== null || chosen.length === 0}
            onClick={() => void accept()}
          >
            {busy === "updating"
              ? "Updating…"
              : `Accept ${chosen.length} update${chosen.length === 1 ? "" : "s"} (${check?.latest_commit.slice(0, 7)})`}
          </button>
        </div>
      )}
    </div>
  );
}

function commitFailure(status: MutationStatus): string {
  return `Saved, but not committed: ${status.error ?? status.path}. Re-apply the package to retry.`;
}

/** Where an item from a package comes from, and checking its package for updates. */
export function PackageOriginPanel({
  path,
  origin,
  query,
  definedHere,
  onUpdated,
}: {
  path: string;
  origin: PackageOrigin;
  query: LevelQuery;
  definedHere: boolean;
  onUpdated: () => Promise<void>;
}) {
  const [checking, setChecking] = useState(false);
  return (
    <div className="skill-origin">
      <p className="small">
        <span className="skill-badge">Package</span> <strong>{origin.name}</strong> ·{" "}
        <a href={origin.url} target="_blank" rel="noreferrer">
          {origin.repository}
        </a>{" "}
        · {origin.ref} ·{" "}
        <a href={origin.commit_url} target="_blank" rel="noreferrer" className="mono">
          {origin.commit.slice(0, 7)}
        </a>
        {origin.changed_locally && <span className="skill-badge skill-badge-changed">Changed locally</span>}
        {definedHere && !checking && (
          <>
            {" · "}
            <button type="button" className="link-button" onClick={() => setChecking(true)}>
              Check for updates
            </button>
          </>
        )}
      </p>
      {checking && (
        <PackageUpdateReview
          name={origin.name}
          query={query}
          focus={path}
          onClose={() => setChecking(false)}
          onUpdated={async () => {
            setChecking(false);
            await onUpdated();
          }}
        />
      )}
    </div>
  );
}
