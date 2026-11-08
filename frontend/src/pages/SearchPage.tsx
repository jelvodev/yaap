import { type FormEvent, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { type Project, api, errorMessage } from "../api/client";
import type { components } from "../api/schema";
import Snippet from "../components/Snippet";
import { useEnvironment } from "../environment/EnvironmentProvider";

type SearchHit = components["schemas"]["SearchHitOut"];
type HitKind = SearchHit["kind"];

const KIND_FILTERS: { value: HitKind | ""; label: string }[] = [
  { value: "", label: "Everything" },
  { value: "message", label: "Conversations" },
  { value: "memory", label: "Project memory" },
  { value: "knowledge", label: "Project knowledge" },
  { value: "blueprint", label: "Blueprint" },
];

const LEVEL_LABELS = { global: "Global", environment: "Environment", project: "Project" } as const;

/**
 * Search conversations, project memory, project knowledge and Blueprint files of the active environment: by words, and by meaning
 * when the embedding model is on.
 */
export default function SearchPage() {
  const { active } = useEnvironment();
  const [params, setParams] = useSearchParams();
  const query = params.get("q") ?? "";
  const projectFilter = params.get("project") ?? "";
  const kindFilter = (params.get("kind") ?? "") as HitKind | "";
  const [draft, setDraft] = useState(query);
  const [projects, setProjects] = useState<Project[]>([]);
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => setDraft(query), [query]);

  useEffect(() => {
    if (!active) return;
    void api
      .GET("/api/v1/environments/{environment_id}/projects", { params: { path: { environment_id: active.id } } })
      .then(({ data }) => setProjects(data ?? []));
  }, [active]);

  useEffect(() => {
    if (!active || !query.trim()) {
      setHits(null);
      return;
    }
    let current = true;
    setError(null);
    void api
      .GET("/api/v1/environments/{environment_id}/search", {
        params: {
          path: { environment_id: active.id },
          query: {
            q: query,
            project_id: projectFilter ? Number(projectFilter) : undefined,
            kind: kindFilter ? [kindFilter] : undefined,
          },
        },
      })
      .then(({ data, error: failure }) => {
        if (!current) return;
        if (failure) setError(errorMessage(failure));
        setHits(data ?? []);
      });
    return () => {
      current = false;
    };
  }, [active, query, projectFilter, kindFilter]);

  function submit(event: FormEvent) {
    event.preventDefault();
    const next = new URLSearchParams(params);
    if (draft.trim()) next.set("q", draft.trim());
    else next.delete("q");
    setParams(next);
  }

  function choose(name: "project" | "kind", value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    setParams(next);
  }

  function hitLink(hit: SearchHit): string {
    if (hit.kind === "message") return `/chat/${hit.conversation_id}?message=${hit.message_id}`;
    if (hit.kind === "memory") return `/chat?project=${hit.project_id}&panel=memory`;
    if (hit.kind === "knowledge") return `/chat?project=${hit.project_id}&panel=knowledge`;
    const level = new URLSearchParams({ level: hit.level ?? "global", path: hit.path ?? "" });
    if (hit.level !== "global" && active) level.set("environment", String(active.id));
    if (hit.level === "project" && hit.project_id) level.set("project", String(hit.project_id));
    return `/blueprint?${level.toString()}`;
  }

  function hitTitle(hit: SearchHit): string {
    if (hit.kind === "message") return hit.conversation_title ?? "Conversation";
    if (hit.kind === "memory") return `Project memory · ${projectName(hit.project_id ?? null) ?? "Project"}`;
    if (hit.kind === "knowledge") return hit.path ?? "Knowledge document";
    return hit.path ?? "Blueprint";
  }

  function hitMeta(hit: SearchHit): string {
    if (hit.kind === "message") {
      const project = hit.project_id != null ? projectName(hit.project_id) : undefined;
      const date = hit.created_at ? new Date(hit.created_at).toLocaleDateString() : "";
      return [hit.role === "user" ? "You" : "YAAP", project, date].filter(Boolean).join(" · ");
    }
    if (hit.kind === "memory") return "Memory";
    if (hit.kind === "knowledge") return ["Knowledge", projectName(hit.project_id ?? null)].filter(Boolean).join(" · ");
    const where =
      hit.level === "project" ? projectName(hit.project_id ?? null) : hit.level === "environment" ? active?.name : undefined;
    return ["Blueprint", hit.level ? LEVEL_LABELS[hit.level] : undefined, where].filter(Boolean).join(" · ");
  }

  const projectName = (id: number | null) => projects.find((project) => project.id === id)?.name;

  return (
    <div className="page-narrow">
      <h1>Search · {active?.name}</h1>
      <form className="card form-row search-form" onSubmit={submit} role="search">
        <label className="visually-hidden" htmlFor="search-query">
          Search
        </label>
        <input
          id="search-query"
          value={draft}
          autoFocus
          placeholder="Words or a question: conversations, memory, knowledge, Blueprint"
          onChange={(event) => setDraft(event.target.value)}
        />
        <label className="visually-hidden" htmlFor="search-project">
          Project
        </label>
        <select id="search-project" value={projectFilter} onChange={(event) => choose("project", event.target.value)}>
          <option value="">All projects</option>
          {projects.map((project) => (
            <option key={project.id} value={project.id}>
              {project.name}
            </option>
          ))}
        </select>
        <button type="submit" className="button button-primary">
          Search
        </button>
      </form>
      <div className="segmented search-kinds" role="group" aria-label="What to search">
        {KIND_FILTERS.map((filter) => (
          <button
            key={filter.value}
            type="button"
            aria-pressed={kindFilter === filter.value}
            onClick={() => choose("kind", filter.value)}
          >
            {filter.label}
          </button>
        ))}
      </div>
      {error && <p className="form-error">{error}</p>}
      {hits !== null && (
        <p className="muted small">
          {hits.length === 0
            ? `Nothing found for “${query}”.`
            : `${hits.length}${hits.length === 50 ? "+" : ""} result${hits.length === 1 ? "" : "s"}, best match first.`}
        </p>
      )}
      <ul className="search-results">
        {hits?.map((hit) => (
          <li key={`${hit.kind}-${hit.message_id ?? ""}-${hit.project_id ?? ""}-${hit.level ?? ""}-${hit.path ?? ""}`}>
            <Link className="search-hit" to={hitLink(hit)}>
              <span className="search-hit-head">
                <strong>{hitTitle(hit)}</strong>
                <span className="faint small">{hitMeta(hit)}</span>
              </span>
              <span className="muted small">
                <Snippet text={hit.snippet} />
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
