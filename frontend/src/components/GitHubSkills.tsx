import { useState } from "react";

import { api, errorMessage } from "../api/client";
import type { components } from "../api/schema";
import DiffView from "./DiffView";
import MarkedText from "./MarkedText";

type Candidate = components["schemas"]["SkillCandidateOut"];
type Preview = components["schemas"]["GitHubPreviewOut"];
type Origin = components["schemas"]["SkillOriginOut"];
type UpdateCheck = components["schemas"]["SkillUpdateOut"];
type LevelQuery = { environment_id?: number; project_id?: number };

const SKILL_NAME = /^[a-z0-9][a-z0-9-]{0,59}$/;

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

/** One skill to review: name, description, warnings, and its text and files on request. */
function CandidateReview({
  candidate,
  chosen,
  name,
  onChoose,
  onRename,
}: {
  candidate: Candidate;
  chosen: boolean;
  name: string;
  onChoose?: (chosen: boolean) => void;
  onRename?: (name: string) => void;
}) {
  const [show, setShow] = useState<"text" | "files" | null>(null);
  return (
    <li className={chosen ? "import-item" : "import-item import-off"}>
      <div className="form-row">
        {onChoose && (
          <input
            type="checkbox"
            checked={chosen}
            disabled={Boolean(candidate.error)}
            aria-label={`Install ${candidate.name}`}
            onChange={(event) => onChoose(event.target.checked)}
          />
        )}
        {onRename ? (
          <input value={name} aria-label="Skill name" onChange={(event) => onRename(event.target.value.toLowerCase())} />
        ) : (
          <strong>{name}</strong>
        )}
        <span className="mono small faint" title={candidate.path}>
          {candidate.path || "/"}
        </span>
      </div>
      <p className="small">{candidate.description || <span className="faint">No description.</span>}</p>
      {candidate.error && <p className="form-error">{candidate.error}</p>}
      {candidate.warnings.length > 0 && (
        <ul className="skill-warnings">
          {candidate.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}
      <p className="faint small">
        {candidate.files.length} file{candidate.files.length === 1 ? "" : "s"} · {formatSize(candidate.size)} ·{" "}
        <button type="button" className="link-button" onClick={() => setShow(show === "text" ? null : "text")}>
          {show === "text" ? "Hide text" : "Show SKILL.md"}
        </button>{" "}
        ·{" "}
        <button type="button" className="link-button" onClick={() => setShow(show === "files" ? null : "files")}>
          {show === "files" ? "Hide files" : "Show files"}
        </button>
      </p>
      {show === "text" && (
        <pre className="import-preview">
          <MarkedText text={candidate.text_marked} />
        </pre>
      )}
      {show === "files" && (
        <pre className="import-preview">
          {candidate.files.map((file) => `${file.path}  (${formatSize(file.size)})`).join("\n")}
        </pre>
      )}
    </li>
  );
}

/** Install skills from a GitHub link into one level, after reviewing them. */
export function GitHubInstallPanel({
  query,
  levelName,
  onClose,
  onInstalled,
}: {
  query: LevelQuery;
  levelName: string;
  onClose: () => void;
  onInstalled: (names: string[]) => void;
}) {
  const [url, setUrl] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [choices, setChoices] = useState<Record<string, { chosen: boolean; name: string }>>({});
  const [busy, setBusy] = useState<"reading" | "installing" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function read() {
    setBusy("reading");
    setError(null);
    setPreview(null);
    const { data, error: failure } = await api.POST("/api/v1/blueprint/skills/github/preview", {
      params: { query },
      body: { url: url.trim() },
    });
    setBusy(null);
    if (!data) {
      setError(errorMessage(failure, "The link could not be read."));
      return;
    }
    setPreview(data);
    setChoices(
      Object.fromEntries(
        data.candidates.map((candidate) => [
          candidate.path,
          { chosen: data.candidates.length === 1 && !candidate.error, name: candidate.name },
        ]),
      ),
    );
  }

  const chosen = preview ? preview.candidates.filter((candidate) => choices[candidate.path]?.chosen) : [];
  const names = chosen.map((candidate) => choices[candidate.path].name);
  const invalid = names.find((name) => !SKILL_NAME.test(name));
  const duplicate = names.find((name, index) => names.indexOf(name) !== index);

  async function install() {
    if (!preview) return;
    setBusy("installing");
    setError(null);
    const { data, error: failure } = await api.POST("/api/v1/blueprint/skills/github/install", {
      params: { query },
      body: {
        url: url.trim(),
        ref: preview.source.ref,
        commit: preview.source.commit,
        skills: chosen.map((candidate) => ({ path: candidate.path, name: choices[candidate.path].name })),
      },
    });
    setBusy(null);
    if (!data) {
      setError(errorMessage(failure, "The skills could not be installed."));
      return;
    }
    const failedCommit = data.commits.find((status) => !status.committed);
    if (failedCommit) {
      setError(`Saved, but not committed: ${failedCommit.error ?? failedCommit.path}. Re-apply the skill to retry.`);
      return;
    }
    onInstalled(data.skills.map((skill) => skill.name));
  }

  return (
    <section className="card import-panel">
      <div className="card-header">
        <h3>Install from GitHub into {levelName}</h3>
        <button type="button" className="button button-ghost" onClick={onClose}>
          Close
        </button>
      </div>
      <p className="muted small">
        Paste a link to a repository, a folder or a SKILL.md. YAAP reads it at one fixed commit and shows what it found;
        nothing is installed or run before you confirm. Private repositories use this environment&apos;s GitHub token.
      </p>
      <form
        className="form-row"
        onSubmit={(event) => {
          event.preventDefault();
          if (url.trim()) void read();
        }}
      >
        <label className="visually-hidden" htmlFor="github-skill-url">
          GitHub link
        </label>
        <input
          id="github-skill-url"
          className="grow"
          value={url}
          placeholder="https://github.com/owner/repository/tree/main/skills/name"
          onChange={(event) => setUrl(event.target.value)}
        />
        <button type="submit" className="button" disabled={busy !== null || !url.trim()}>
          {busy === "reading" ? "Reading…" : "Read"}
        </button>
      </form>
      {error && <p className="form-error">{error}</p>}
      {preview && (
        <>
          <p className="faint small">
            {preview.source.repository} · {preview.source.ref} ·{" "}
            <a href={preview.source.url} target="_blank" rel="noreferrer" className="mono">
              {preview.source.commit.slice(0, 7)}
            </a>
          </p>
          <ul className="import-list">
            {preview.candidates.map((candidate) => (
              <CandidateReview
                key={candidate.path}
                candidate={candidate}
                chosen={choices[candidate.path]?.chosen ?? false}
                name={choices[candidate.path]?.name ?? candidate.name}
                onChoose={(value) =>
                  setChoices((current) => ({ ...current, [candidate.path]: { ...current[candidate.path], chosen: value } }))
                }
                onRename={(value) =>
                  setChoices((current) => ({ ...current, [candidate.path]: { ...current[candidate.path], name: value } }))
                }
              />
            ))}
          </ul>
          {invalid !== undefined && <p className="form-error">{invalid || "An empty name"}: use lowercase letters, digits and hyphens.</p>}
          {duplicate && <p className="form-error">Two skills would be named {duplicate}; rename one.</p>}
          <div className="form-row">
            <button
              type="button"
              className="button button-primary"
              disabled={busy !== null || chosen.length === 0 || invalid !== undefined || Boolean(duplicate)}
              onClick={() => void install()}
            >
              {busy === "installing"
                ? "Installing…"
                : `Install ${chosen.length} skill${chosen.length === 1 ? "" : "s"} in ${levelName}`}
            </button>
          </div>
        </>
      )}
    </section>
  );
}

/** Where an installed skill comes from, and checking for and installing an update. */
export function SkillOriginPanel({
  name,
  origin,
  query,
  definedHere,
  onUpdated,
}: {
  name: string;
  origin: Origin;
  query: LevelQuery;
  definedHere: boolean;
  onUpdated: () => Promise<void>;
}) {
  const [check, setCheck] = useState<UpdateCheck | null>(null);
  const [busy, setBusy] = useState<"checking" | "updating" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function checkForUpdates() {
    setBusy("checking");
    setError(null);
    const { data, error: failure } = await api.GET("/api/v1/blueprint/skills/{name}/update", {
      params: { path: { name }, query },
    });
    setBusy(null);
    if (data) setCheck(data);
    else setError(errorMessage(failure, "Checking for updates failed."));
  }

  async function update() {
    if (!check) return;
    setBusy("updating");
    setError(null);
    const { data, error: failure } = await api.POST("/api/v1/blueprint/skills/github/install", {
      params: { query },
      body: { url: check.url, ref: check.ref, commit: check.latest_commit, skills: [{ path: origin.path, name }] },
    });
    setBusy(null);
    if (failure || !data) {
      setError(errorMessage(failure, "The update could not be installed."));
      return;
    }
    const failedCommit = data.commits[0];
    if (failedCommit && !failedCommit.committed) {
      setError(`Saved, but not committed: ${failedCommit.error ?? failedCommit.path}. Re-apply the update to retry.`);
      return;
    }
    setCheck(null);
    await onUpdated();
  }

  return (
    <div className="skill-origin">
      <p className="small">
        <span className="skill-badge">GitHub</span> {origin.repository} · {origin.ref} ·{" "}
        <a href={origin.commit_url} target="_blank" rel="noreferrer" className="mono">
          {origin.commit.slice(0, 7)}
        </a>
        {origin.changed_locally && <span className="skill-badge skill-badge-changed">Changed locally</span>}
        {definedHere && (
          <>
            {" · "}
            <button type="button" className="link-button" disabled={busy !== null} onClick={() => void checkForUpdates()}>
              {busy === "checking" ? "Checking…" : "Check for updates"}
            </button>
          </>
        )}
      </p>
      {error && <p className="form-error">{error}</p>}
      {check && check.up_to_date && (
        <p className="muted small">
          Up to date with {check.ref}.{check.changed_locally ? " Your local changes are below." : ""}
        </p>
      )}
      {check && !check.up_to_date && (
        <>
          <p className="muted small">
            {check.ref} moved to{" "}
            <a href={check.source.url} target="_blank" rel="noreferrer" className="mono">
              {check.latest_commit.slice(0, 7)}
            </a>
            . Upstream changes:
          </p>
          <DiffView diff={check.upstream_diff} />
          {check.candidate && (
            <ul className="import-list">
              <CandidateReview candidate={check.candidate} chosen name={name} />
            </ul>
          )}
        </>
      )}
      {check && check.changed_locally && (
        <>
          <p className="muted small">
            Local changes since the install{check.up_to_date ? "" : "; updating replaces them with the new version"}:
          </p>
          <DiffView diff={check.local_diff} />
        </>
      )}
      {check && !check.up_to_date && (
        <div className="form-row">
          <button
            type="button"
            className="button button-primary"
            disabled={busy !== null || Boolean(check.candidate?.error)}
            onClick={() => void update()}
          >
            {busy === "updating" ? "Updating…" : `Update to ${check.latest_commit.slice(0, 7)}`}
          </button>
        </div>
      )}
    </div>
  );
}
