import { useEffect, useState } from "react";

import { type Project, api } from "../../api/client";
import "../dialog/confirm-dialog.css";
import ImportPanel from "../../pages/blueprint/ImportPanel";

/** Offers to import the setup files (Claude Code, Codex, Cursor, Copilot) found in the project's own folder. */
export default function ProjectSetupImport({ project }: { project: Project }) {
  const [count, setCount] = useState(0);
  const [open, setOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    void api
      .GET("/api/v1/blueprint/import/project/found", {
        params: { query: { environment_id: project.environment_id, project_id: project.id } },
      })
      .then(({ data }) => {
        if (current) setCount(data?.count ?? 0);
      });
    return () => {
      current = false;
    };
  }, [project.id, project.environment_id]);

  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  if (count === 0) return null;
  return (
    <section className="pp-section">
      <h3>Setup from other tools</h3>
      <p className="faint small">This project's folder holds setup files for other coding tools. You can bring them into its Blueprint.</p>
      <button type="button" className="button" onClick={() => setOpen(true)}>
        Import this project's setup ({count} item{count === 1 ? "" : "s"})
      </button>
      {notice && <p className="faint small">{notice}</p>}
      {open && (
        <div className="palette-backdrop confirm-backdrop" onMouseDown={() => setOpen(false)}>
          <div
            className="palette confirm-dialog import-dialog"
            role="dialog"
            aria-modal="true"
            aria-label="Import this project's setup"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <ImportPanel
              source="project"
              level={{ kind: "project", environmentId: project.environment_id, projectId: project.id }}
              levelName={project.name}
              utilityEnvironmentId={project.environment_id}
              onClose={() => setOpen(false)}
              onImported={(written) => {
                setOpen(false);
                setNotice(`Imported ${written.length} item${written.length === 1 ? "" : "s"}.`);
              }}
            />
          </div>
        </div>
      )}
    </section>
  );
}
