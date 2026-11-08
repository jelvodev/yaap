import { type FormEvent, useState } from "react";

import { type Project, api, errorMessage } from "../../api/client";

export type NewProject = ReturnType<typeof useNewProject>;

/** The form for a new project in the sidebar: empty, cloned from a repository, or from a GitHub template. */
export function useNewProject(environmentId: number, onCreated: (project: Project) => void) {
  // The name typed so far, or null while the form is closed.
  const [name, setName] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  // A GitHub template repository to start from: owner/name, or "" for none.
  const [template, setTemplate] = useState("");
  const [isPrivate, setPrivate] = useState(true);
  // Template repositories on GitHub, when the environment has a token; none otherwise. Loaded on first open.
  const [templates, setTemplates] = useState<string[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function toggle() {
    setName((current) => (current === null ? "" : null));
    if (templates !== null) return;
    void api
      .GET("/api/v1/environments/{environment_id}/github/repositories", {
        params: { path: { environment_id: environmentId } },
      })
      .then(({ data }) => setTemplates((data ?? []).filter((item) => item.is_template).map((item) => item.full_name)));
  }

  function changeUrl(value: string) {
    setUrl(value);
    // Suggest the repository name as project name.
    const repository = value.trim().match(/\/([^/]+?)(?:\.git)?\/?$/)?.[1];
    if (repository && !name) setName(repository);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const trimmed = (name ?? "").trim();
    if (!trimmed) return;
    setCreating(true);
    const { data, error: requestError } = await api.POST("/api/v1/environments/{environment_id}/projects", {
      params: { path: { environment_id: environmentId } },
      body: template
        ? { name: trimmed, template, private: isPrivate }
        : { name: trimmed, repository_url: url.trim() || null, private: true },
    });
    setCreating(false);
    if (requestError || !data) {
      setError(errorMessage(requestError, "The project could not be created."));
      return;
    }
    setName(null);
    setUrl("");
    setTemplate("");
    setError(null);
    onCreated(data);
  }

  return {
    name,
    url,
    template,
    isPrivate,
    templates,
    creating,
    error,
    toggle,
    close: () => setName(null),
    setName,
    changeUrl,
    setTemplate,
    setPrivate,
    submit,
  };
}
