import { api } from "../../api/client";
import type { components } from "../../api/schema";

export type Board = components["schemas"]["BoardOut"];
export type BoardCard = components["schemas"]["CardOut"];
export type CardDetail = components["schemas"]["CardDetailOut"];
export type BoardVersion = components["schemas"]["BoardVersionOut"];
export type CardCreate = components["schemas"]["CardCreate"];
export type CardPatch = components["schemas"]["CardPatch"];
export type VersionPut = components["schemas"]["VersionPut"];
export type Column = NonNullable<BoardCard["column"]>;
export type CardKind = NonNullable<BoardCard["kind"]>;
export type BoardStartChoice = components["schemas"]["BoardStart"];
export type RemoteCheck = components["schemas"]["RemoteCheckOut"];

/** A refused board request: the backend's code and safe sentence, the flow's reason for a refused move, and the
 * current card when it changed meanwhile. */
export interface BoardProblem {
  code: string | null;
  message: string;
  reason: string | null;
  current: CardDetail | null;
}

export type BoardResult<T> = { ok: true; data: T } | { ok: false; problem: BoardProblem };

function text(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isCardDetail(value: unknown): value is CardDetail {
  return isRecord(value) && typeof value.id === "number" && typeof value.title === "string" && "description" in value;
}

/** Read a board error body (`{detail: {code, message, reason?, current?}}`) or a validation error. */
export function boardProblem(error: unknown, fallback: string): BoardProblem {
  const detail = isRecord(error) ? error.detail : undefined;
  if (isRecord(detail)) {
    return {
      code: text(detail.code),
      message: text(detail.message) ?? fallback,
      reason: text(detail.reason),
      current: isCardDetail(detail.current) ? detail.current : null,
    };
  }
  if (typeof detail === "string") return { code: null, message: detail, reason: null, current: null };
  if (Array.isArray(detail) && isRecord(detail[0])) {
    return { code: null, message: text(detail[0].msg) ?? fallback, reason: null, current: null };
  }
  return { code: null, message: fallback, reason: null, current: null };
}

function result<T>(data: T | undefined, error: unknown, fallback: string): BoardResult<T> {
  if (error !== undefined || data === undefined) return { ok: false, problem: boardProblem(error, fallback) };
  return { ok: true, data };
}

function project(projectId: number) {
  return { project_id: projectId };
}

function card(projectId: number, cardId: number) {
  return { project_id: projectId, card_id: cardId };
}

export async function readBoard(projectId: number, since: string | null): Promise<BoardResult<Board>> {
  const { data, error } = await api.GET("/api/v1/projects/{project_id}/board", {
    params: { path: project(projectId), query: since ? { since } : {} },
  });
  return result(data, error, "The board could not be read.");
}

export async function startBoard(projectId: number, choice: BoardStartChoice): Promise<BoardResult<Board>> {
  const { data, error } = await api.POST("/api/v1/projects/{project_id}/board", {
    params: { path: project(projectId) },
    body: choice,
  });
  return result(data, error, "The board could not be started.");
}

export async function createCard(projectId: number, body: CardCreate): Promise<BoardResult<CardDetail>> {
  const { data, error } = await api.POST("/api/v1/projects/{project_id}/board/cards", {
    params: { path: project(projectId) },
    body,
  });
  return result(data, error, "The card could not be added.");
}

export async function readCard(projectId: number, cardId: number): Promise<BoardResult<CardDetail>> {
  const { data, error } = await api.GET("/api/v1/projects/{project_id}/board/cards/{card_id}", {
    params: { path: card(projectId, cardId) },
  });
  return result(data, error, "The card could not be read.");
}

export async function patchCard(
  projectId: number,
  cardId: number,
  body: CardPatch,
): Promise<BoardResult<CardDetail>> {
  const { data, error } = await api.PATCH("/api/v1/projects/{project_id}/board/cards/{card_id}", {
    params: { path: card(projectId, cardId) },
    body,
  });
  return result(data, error, "The card could not be saved.");
}

export async function moveCard(
  projectId: number,
  cardId: number,
  column: Column,
  expectedUpdated: string | null,
): Promise<BoardResult<CardDetail>> {
  const { data, error } = await api.POST("/api/v1/projects/{project_id}/board/cards/{card_id}/move", {
    params: { path: card(projectId, cardId) },
    body: { column, expected_updated: expectedUpdated },
  });
  return result(data, error, "The card could not be moved.");
}

export async function answerQuestion(
  projectId: number,
  cardId: number,
  index: number,
  answer: string,
): Promise<BoardResult<CardDetail>> {
  const { data, error } = await api.POST("/api/v1/projects/{project_id}/board/cards/{card_id}/answer", {
    params: { path: card(projectId, cardId) },
    body: { index, text: answer },
  });
  return result(data, error, "The answer could not be saved.");
}

export async function dropCard(projectId: number, cardId: number): Promise<BoardResult<null>> {
  const { error, response } = await api.DELETE("/api/v1/projects/{project_id}/board/cards/{card_id}", {
    params: { path: card(projectId, cardId) },
  });
  if (error !== undefined || !response.ok) {
    return { ok: false, problem: boardProblem(error, "The card could not be dropped.") };
  }
  return { ok: true, data: null };
}

export async function putVersion(
  projectId: number,
  name: string,
  body: VersionPut,
): Promise<BoardResult<BoardVersion>> {
  const { data, error } = await api.PUT("/api/v1/projects/{project_id}/board/versions/{name}", {
    params: { path: { project_id: projectId, name } },
    body,
  });
  return result(data, error, "The version could not be saved.");
}

export async function pushBoard(projectId: number, acknowledgePublic: boolean): Promise<BoardResult<string>> {
  const { data, error } = await api.POST("/api/v1/projects/{project_id}/board/push", {
    params: { path: project(projectId) },
    body: { acknowledge_public: acknowledgePublic },
  });
  return result(data?.pushed_commit, error, "The board could not be pushed.");
}

/** Fetch a repository board's remote; the head is null when there was nothing new. */
export async function pullBoard(projectId: number): Promise<BoardResult<string | null>> {
  const { data, error } = await api.POST("/api/v1/projects/{project_id}/board/pull", {
    params: { path: project(projectId) },
  });
  if (error !== undefined || data === undefined) {
    return { ok: false, problem: boardProblem(error, "The board could not be pulled.") };
  }
  return { ok: true, data: data.head };
}

export async function checkRemote(projectId: number, url: string): Promise<BoardResult<RemoteCheck>> {
  const { data, error } = await api.POST("/api/v1/projects/{project_id}/board/check-remote", {
    params: { path: project(projectId) },
    body: { repository_url: url },
  });
  return result(data, error, "The repository could not be checked.");
}

/** The cards of a board answer in column order, broken cards last. */
export function boardCards(board: Board): BoardCard[] {
  const columns = board.columns ?? {};
  return [...Object.values(columns).flatMap((column) => column?.cards ?? []), ...board.broken];
}
