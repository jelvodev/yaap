import { api, errorMessage } from "./client";
import type { components } from "./schema";

/** The subscription CLIs that can be signed in to from the browser; the names are the API's path parts. */
export type CliName = "claude-code" | "codex";
export type SignIn = components["schemas"]["SignInOut"];

export type SignInResult = { sign_in: SignIn; error?: undefined } | { sign_in?: undefined; error: string };

function result(data: SignIn | undefined, error: unknown): SignInResult {
  return data ? { sign_in: data } : { error: errorMessage(error, "The sign-in did not respond.") };
}

export async function readSignIn(cli: CliName): Promise<SignInResult> {
  const { data, error } =
    cli === "codex" ? await api.GET("/api/v1/codex/sign-in") : await api.GET("/api/v1/claude-code/sign-in");
  return result(data, error);
}

export async function startSignIn(cli: CliName): Promise<SignInResult> {
  const { data, error } =
    cli === "codex" ? await api.POST("/api/v1/codex/sign-in") : await api.POST("/api/v1/claude-code/sign-in");
  return result(data, error);
}

export async function sendSignInCode(cli: CliName, code: string): Promise<SignInResult> {
  const { data, error } =
    cli === "codex"
      ? await api.POST("/api/v1/codex/sign-in/code", { body: { code } })
      : await api.POST("/api/v1/claude-code/sign-in/code", { body: { code } });
  return result(data, error);
}

export async function cancelSignIn(cli: CliName): Promise<SignInResult> {
  const { data, error } =
    cli === "codex" ? await api.DELETE("/api/v1/codex/sign-in") : await api.DELETE("/api/v1/claude-code/sign-in");
  return result(data, error);
}
