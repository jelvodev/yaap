import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

// Through window.fetch at call time, so the wrapper of auth-fetch.ts (CSRF token, signed-out notice) applies.
export const api = createClient<paths>({ baseUrl: "", fetch: (request) => window.fetch(request) });

export type Environment = components["schemas"]["EnvironmentOut"];
export type CredentialStatus = components["schemas"]["CredentialStatusOut"];
export type ModelInfo = components["schemas"]["ModelOut"];
export type EndpointStatus = components["schemas"]["EndpointOut"];
export type ConversationSummary = components["schemas"]["ConversationSummaryOut"];
export type Conversation = components["schemas"]["ConversationOut"];
export type Message = components["schemas"]["MessageOut"];
export type Setting = components["schemas"]["SettingOut"];
export type ClaudeCodeStatus = components["schemas"]["ClaudeCodeStatusOut"];
export type CodexStatus = components["schemas"]["CodexStatusOut"];
export type Project = components["schemas"]["ProjectOut"];
export type BlueprintItem = components["schemas"]["BlueprintItemOut"];
export type BlueprintFile = components["schemas"]["BlueprintFileOut"];
export type MutationStatus = components["schemas"]["MutationStatusOut"];
export type GitRepository = components["schemas"]["RepositoryOut"];
export type Preset = components["schemas"]["PresetOut"];
export type Why = components["schemas"]["WhyOut"];
export type Behind = components["schemas"]["BehindOut"];
export type BehindLabel = components["schemas"]["BehindLabelOut"];
export type CallWhy = components["schemas"]["CallWhyOut"];

/** Turn an error body from the API into one readable sentence. */
export function errorMessage(error: unknown, fallback = "Something went wrong."): string {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: string };
      if (first.msg) return first.msg;
    }
  }
  if (error instanceof Error) return error.message;
  return fallback;
}
