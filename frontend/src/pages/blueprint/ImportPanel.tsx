import { useState } from "react";

import { type BlueprintItem, api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";
import MarkedText, { ReviewWarnings } from "../../components/MarkedText";
import { type Level, levelQuery } from "../../components/workbench/LevelPicker";
import { type ItemKind, KINDS, pathFor } from "./model";

type ImportProposal = components["schemas"]["ImportProposalOut"] & { include: boolean };

/** Import a Markdown file or a zip of them: the utility model proposes items, the user reviews, then they are written. */
export default function ImportPanel({
  level,
  levelName,
  utilityEnvironmentId,
  items,
  onClose,
  onImported,
}: {
  level: Level;
  levelName: string;
  utilityEnvironmentId: number | null;
  items: BlueprintItem[];
  onClose: () => void;
  onImported: (written: string[]) => void;
}) {
  const [proposals, setProposals] = useState<ImportProposal[] | null>(null);
  const [busy, setBusy] = useState<"analyzing" | "writing" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<number | null>(null);

  async function analyze(file: File) {
    setBusy("analyzing");
    setError(null);
    setProposals(null);
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(levelQuery(level))) query.set(key, String(value));
    if (utilityEnvironmentId !== null) query.set("utility_environment_id", String(utilityEnvironmentId));
    const form = new FormData();
    form.append("file", file, file.name);
    try {
      const response = await fetch(`/api/v1/blueprint/import/analyze?${query}`, { method: "POST", body: form });
      const body = await response.json().catch(() => null);
      if (!response.ok) throw new Error(errorMessage(body, `${file.name} could not be read (HTTP ${response.status}).`));
      setProposals((body as components["schemas"]["ImportProposalOut"][]).map((item) => ({ ...item, include: true })));
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  function change(index: number, patch: Partial<ImportProposal>) {
    setProposals((current) => current && current.map((item, position) => (position === index ? { ...item, ...patch } : item)));
  }

  const chosen = (proposals ?? []).filter((item) => item.include);
  const paths = chosen.map((item) => pathFor(item.kind, item.name));
  const duplicate = paths.find((path, index) => paths.indexOf(path) !== index);
  const invalid = chosen.find((item) => item.kind !== "instructions" && !/^[a-z0-9][a-z0-9-]{0,59}$/.test(item.name));

  async function apply() {
    setBusy("writing");
    setError(null);
    const { data, error: failure } = await api.POST("/api/v1/blueprint/import/apply", {
      params: { query: levelQuery(level) },
      body: {
        items: chosen.map((item) => ({
          kind: item.kind,
          name: item.name,
          summary: item.summary,
          rules: item.rules,
          body: item.body,
          extra: item.extra,
        })),
      },
    });
    setBusy(null);
    if (failure || !data) {
      setError(errorMessage(failure, "The items could not be written."));
      return;
    }
    const failedCommit = data.commits.find((commit) => !commit.committed);
    if (failedCommit) {
      setError(`Saved, but not committed: ${failedCommit.error ?? failedCommit.path}. Re-apply the import to retry.`);
      return;
    }
    onImported(data.written);
  }

  return (
    <section className="card import-panel">
      <div className="card-header">
        <h3>Import into {levelName}</h3>
        <button type="button" className="button button-ghost" onClick={onClose}>
          Close
        </button>
      </div>
      <p className="muted small">
        Upload a Markdown file or a zip of Markdown files. YAAP proposes for each document what it is, a name, a summary
        and rules; you review them before anything is written. Each item becomes its own commit.
      </p>
      <label className="button">
        {busy === "analyzing" ? "Reading…" : "Choose .md or .zip…"}
        <input
          type="file"
          accept=".md,.markdown,.txt,.zip"
          className="visually-hidden"
          disabled={busy !== null}
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (file) void analyze(file);
          }}
        />
      </label>
      {error && <p className="form-error">{error}</p>}

      {proposals && (
        <>
          <ul className="import-list">
            {proposals.map((item, index) => {
              const path = pathFor(item.kind, item.name);
              const exists = items.some((existing) => existing.path === path && existing.defined_here);
              return (
                <li key={`${item.source}-${index}`} className={item.include ? "import-item" : "import-item import-off"}>
                  <div className="form-row">
                    <input
                      type="checkbox"
                      checked={item.include}
                      aria-label={`Import ${item.source}`}
                      onChange={(event) => change(index, { include: event.target.checked })}
                    />
                    <span className="mono small import-source" title={item.source}>
                      {item.source}
                    </span>
                    <select
                      value={item.kind}
                      aria-label="Kind"
                      onChange={(event) => change(index, { kind: event.target.value as ItemKind })}
                    >
                      {KINDS.map((section) => (
                        <option key={section.kind} value={section.kind}>
                          {section.title}
                        </option>
                      ))}
                    </select>
                    {item.kind !== "instructions" && (
                      <input
                        value={item.name}
                        aria-label="Name"
                        onChange={(event) => change(index, { name: event.target.value.toLowerCase() })}
                      />
                    )}
                  </div>
                  <input
                    className="import-summary"
                    value={item.summary}
                    placeholder="Summary"
                    aria-label="Summary"
                    onChange={(event) => change(index, { summary: event.target.value })}
                  />
                  {(item.kind === "standard" || item.kind === "engineering") && (
                    <textarea
                      className="import-rules"
                      rows={Math.min(Math.max(item.rules.length, 2), 8)}
                      value={item.rules.join("\n")}
                      placeholder="One rule per line"
                      aria-label="Rules"
                      onChange={(event) =>
                        change(index, { rules: event.target.value.split("\n").filter((rule, position, all) => rule.trim() || position === all.length - 1) })
                      }
                    />
                  )}
                  <ReviewWarnings warnings={item.warnings} />
                  <p className="faint small">
                    {path}
                    {item.proposed_by_model ? " · proposed by the utility model" : ""}
                    {exists && <span className="import-warning"> · replaces the existing item in {levelName}</span>}
                    {" · "}
                    <button type="button" className="link-button" onClick={() => setPreview(preview === index ? null : index)}>
                      {preview === index ? "Hide text" : "Show text"}
                    </button>
                  </p>
                  {preview === index && (
                    <pre className="import-preview">
                      <MarkedText text={item.body_marked} />
                    </pre>
                  )}
                </li>
              );
            })}
          </ul>
          {duplicate && <p className="form-error">Two items would be written to {duplicate}; rename or skip one.</p>}
          {invalid && <p className="form-error">{invalid.name || "An empty name"}: use lowercase letters, digits and hyphens.</p>}
          <div className="form-row">
            <button
              type="button"
              className="button button-primary"
              disabled={busy !== null || chosen.length === 0 || Boolean(duplicate) || Boolean(invalid)}
              onClick={() => void apply()}
            >
              {busy === "writing" ? "Writing…" : `Import ${chosen.length} item${chosen.length === 1 ? "" : "s"}`}
            </button>
          </div>
        </>
      )}
    </section>
  );
}
