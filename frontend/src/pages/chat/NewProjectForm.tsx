import type { NewProject } from "./useNewProject";

function submitLabel(form: NewProject): string {
  if (!form.creating) return "Create project";
  if (form.template) return "Creating from the template…";
  return form.url.trim() ? "Cloning…" : "Creating…";
}

/** A new project: empty, cloned from GitHub, or from one of the environment's template repositories. */
export default function NewProjectForm({ form }: { form: NewProject }) {
  if (form.name === null) return null;
  return (
    <form className="project-form" onSubmit={(event) => void form.submit(event)}>
      <label className="visually-hidden" htmlFor="new-project-name">
        Project name
      </label>
      <input
        id="new-project-name"
        autoFocus
        value={form.name}
        maxLength={120}
        placeholder="Project name"
        onChange={(event) => form.setName(event.target.value)}
        onKeyDown={(event) => event.key === "Escape" && form.close()}
      />
      {form.templates && form.templates.length > 0 && (
        <>
          <label className="visually-hidden" htmlFor="new-project-template">
            Start from
          </label>
          <select
            id="new-project-template"
            value={form.template}
            onChange={(event) => form.setTemplate(event.target.value)}
          >
            <option value="">Empty, or clone a repository</option>
            {form.templates.map((template) => (
              <option key={template} value={template}>
                From the template {template}
              </option>
            ))}
          </select>
        </>
      )}
      {form.template ? (
        <label className="checkbox-row small">
          <input type="checkbox" checked={form.isPrivate} onChange={(event) => form.setPrivate(event.target.checked)} />
          Private repository on GitHub, named after the project
        </label>
      ) : (
        <>
          <label className="visually-hidden" htmlFor="new-project-url">
            Clone from GitHub (optional)
          </label>
          <input
            id="new-project-url"
            value={form.url}
            maxLength={400}
            placeholder="Clone from https://github.com/… (optional)"
            onChange={(event) => form.changeUrl(event.target.value)}
          />
        </>
      )}
      <button type="submit" className="button" disabled={form.creating || !form.name.trim()}>
        {submitLabel(form)}
      </button>
      {form.error && <p className="form-error">{form.error}</p>}
    </form>
  );
}
