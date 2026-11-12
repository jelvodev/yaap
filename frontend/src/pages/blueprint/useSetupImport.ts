import { useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";
import { type Level, levelQuery } from "../../components/workbench/LevelPicker";
import {
  type ImportChoice,
  type Layout,
  type Proposal,
  SOURCES,
  duplicatePath,
  effectiveLayer,
  initialChoice,
  validName,
} from "./importLogic";
import { pathFor } from "./model";

type Analysis = components["schemas"]["ImportAnalysisOut"];

/** A proposal with the reviewer's edits and choices on top. */
export type ImportRow = Proposal & ImportChoice;

export type NotImported = Analysis["not_imported"];

/** Where the setup is read from: an uploaded file, or the project's own folder. */
export type ImportSource = "upload" | "project";

function toRows(analysis: Analysis): ImportRow[] {
  return analysis.proposals.map((proposal) => ({ ...proposal, ...initialChoice(proposal) }));
}

/** The review of an imported setup: reading it, the edits per item, and writing the ticked items. */
export function useSetupImport({
  level,
  utilityEnvironmentId,
  onImported,
}: {
  level: Level;
  utilityEnvironmentId: number | null;
  onImported: (written: string[]) => void;
}) {
  const [rows, setRows] = useState<ImportRow[] | null>(null);
  const [notImported, setNotImported] = useState<NotImported>([]);
  const [skipped, setSkipped] = useState(0);
  const [busy, setBusy] = useState<"analyzing" | "writing" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [layout, setLayout] = useState<Layout | "markdown">("markdown");

  const levelIsGlobal = level.kind === "global";
  const utility = utilityEnvironmentId === null ? {} : { utility_environment_id: utilityEnvironmentId };

  function show(analysis: Analysis) {
    setRows(toRows(analysis));
    setNotImported(analysis.not_imported);
    setSkipped(analysis.skipped);
  }

  async function analyzeUpload(file: File) {
    setBusy("analyzing");
    setError(null);
    setRows(null);
    const form = new FormData();
    form.append("file", file, file.name);
    const chosen = SOURCES.find((entry) => entry.layout === layout);
    const { data, error: failure } = await api.POST("/api/v1/blueprint/import/analyze", {
      params: {
        query: {
          ...levelQuery(level),
          ...utility,
          origin: chosen?.origin ?? "project",
          layout: layout === "markdown" ? null : layout,
        },
      },
      // The generated body type is a placeholder for the file; the serializer sends the real multipart form.
      body: { file: file.name },
      bodySerializer: () => form,
    });
    setBusy(null);
    if (failure || !data) setError(errorMessage(failure, `${file.name} could not be read.`));
    else show(data);
  }

  async function analyzeProject() {
    setBusy("analyzing");
    setError(null);
    setRows(null);
    const { data, error: failure } = await api.POST("/api/v1/blueprint/import/project", {
      params: { query: { ...levelQuery(level), ...utility } },
    });
    setBusy(null);
    if (failure || !data) setError(errorMessage(failure, "The project's setup could not be read."));
    else show(data);
  }

  function change(index: number, patch: Partial<ImportRow>) {
    setRows((current) => current && current.map((row, position) => (position === index ? { ...row, ...patch } : row)));
  }

  function toggleFile(index: number, fileIndex: number, on: boolean) {
    setRows(
      (current) =>
        current &&
        current.map((row, position) =>
          position === index ? { ...row, fileOn: row.fileOn.map((was, at) => (at === fileIndex ? on : was)) } : row,
        ),
    );
  }

  const chosen = (rows ?? []).filter((row) => row.include);
  const duplicate = duplicatePath(rows ?? [], levelIsGlobal, pathFor);
  const invalid = chosen.find((row) => !validName(row));

  async function apply() {
    setBusy("writing");
    setError(null);
    const { data, error: failure } = await api.POST("/api/v1/blueprint/import/apply", {
      params: { query: levelQuery(level) },
      body: {
        items: chosen.map((row) => ({
          kind: row.kind,
          name: row.name,
          summary: row.summary,
          rules: row.rules,
          body: row.body,
          extra: row.extra,
          layer: effectiveLayer(row.layer, levelIsGlobal),
          files:
            row.kind === "skill"
              ? row.files.filter((_, at) => row.fileOn[at]).map((file) => ({ path: file.path, content: file.content }))
              : [],
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

  return {
    rows,
    notImported,
    skipped,
    busy,
    error,
    layout,
    setLayout,
    levelIsGlobal,
    chosen,
    duplicate,
    invalid,
    change,
    toggleFile,
    analyzeUpload,
    analyzeProject,
    apply,
  };
}
