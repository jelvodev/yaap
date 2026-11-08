// ---------------------------------------------------------------------------------------------------------------
// The plugin SDK. Above: YAAP's API as openapi-typescript describes it (paths, components, operations).
// ---------------------------------------------------------------------------------------------------------------

/** The version of YAAP's API this SDK was generated from. */
export type ApiVersion = __API_VERSION__;

export type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
type Lower<M extends Method> = M extends "GET"
  ? "get"
  : M extends "POST"
    ? "post"
    : M extends "PUT"
      ? "put"
      : M extends "PATCH"
        ? "patch"
        : "delete";

/** The paths of YAAP's API that answer this method. */
export type PathsWith<M extends Method> = {
  [P in keyof paths]: paths[P] extends { [K in Lower<M>]: object } ? P : never;
}[keyof paths];

/** The operation behind a method and path. */
export type Operation<M extends Method, P extends PathsWith<M>> = paths[P] extends { [K in Lower<M>]: infer O }
  ? O
  : never;

type PathParams<O> = O extends { parameters: { path: infer X } } ? ([X] extends [never] ? never : X) : never;
type QueryParams<O> = O extends { parameters: { query?: infer X } } ? ([X] extends [never] ? never : X) : never;
type QueryRequired<O> = O extends { parameters: { query: object } } ? true : false;
type JsonBody<O> = O extends { requestBody: { content: { "application/json": infer B } } } ? B : never;
type OptionalJsonBody<O> = O extends { requestBody?: { content: { "application/json": infer B } } } ? B : never;

type QueryInit<O> = [QueryParams<O>] extends [never]
  ? { query?: never }
  : QueryRequired<O> extends true
    ? { query: QueryParams<O> }
    : { query?: QueryParams<O> };
type ParamsInit<O> = [PathParams<O>] extends [never]
  ? [QueryParams<O>] extends [never]
    ? { params?: never }
    : QueryRequired<O> extends true
      ? { params: QueryInit<O> }
      : { params?: QueryInit<O> }
  : { params: { path: PathParams<O> } & QueryInit<O> };
type BodyInit<O> = [JsonBody<O>] extends [never]
  ? [OptionalJsonBody<O>] extends [never]
    ? { body?: never }
    : { body?: OptionalJsonBody<O> }
  : { body: JsonBody<O> };

/** What a call takes besides the path: path and query parameters, a JSON body and an AbortSignal. */
export type CallInit<O> = ParamsInit<O> &
  BodyInit<O> & {
    signal?: AbortSignal;
  };

type NeedsInit<O> = [PathParams<O>] extends [never]
  ? [JsonBody<O>] extends [never]
    ? QueryRequired<O>
    : true
  : true;

type SuccessStatus = 200 | 201 | 202 | 203 | 204;
/** The JSON a successful call returns; undefined for 204 No Content. */
export type ResponseData<O> = O extends { responses: infer R }
  ? {
      [S in keyof R & SuccessStatus]: R[S] extends { content: { "application/json": infer D } } ? D : undefined;
    }[keyof R & SuccessStatus]
  : unknown;

/** What YAAP answers when it refuses a call: usually { detail: "why" }. */
export interface ApiError {
  detail?: string | components["schemas"]["ValidationError"][];
}

/** A call's outcome with the HTTP status YAAP answered. */
export type ApiResult<O> =
  | { data: ResponseData<O>; error?: undefined; status: number }
  | { data?: undefined; error: ApiError; status: number };

type Call<M extends Method> = <P extends PathsWith<M>>(
  path: P,
  ...init: NeedsInit<Operation<M, P>> extends true
    ? [init: CallInit<Operation<M, P>>]
    : [init?: CallInit<Operation<M, P>>]
) => Promise<ApiResult<Operation<M, P>>>;

/**
 * YAAP's API, called through the YAAP page around the frame with the user's session. A path is written as YAAP's
 * OpenAPI description writes it; only the routes the plugin's manifest lists under `api` are made, the rest are
 * refused with 403. A call rejects when YAAP does not answer in time or the signal aborts it.
 */
export interface Client {
  GET: Call<"GET">;
  POST: Call<"POST">;
  PUT: Call<"PUT">;
  PATCH: Call<"PATCH">;
  DELETE: Call<"DELETE">;
}

/** A CSS custom property of YAAP's theme that the SDK puts on the page. */
export type ThemeToken = __THEME_TOKENS__;

export interface Theme {
  scheme: "dark" | "light";
  tokens: Partial<Record<ThemeToken, string>>;
}

export interface PluginEnvironment {
  id: number;
  name: string;
  colour: string;
}

export interface PluginProject {
  id: number;
  name: string;
}

/** The environment and project active in YAAP; project is null outside a project. */
export interface PluginContext {
  environment: PluginEnvironment;
  project: PluginProject | null;
}

export interface Events {
  /** The active environment or project changed. */
  context: PluginContext;
  /** The user switched between the dark and light theme, or the accent changed with the environment. */
  theme: Theme;
}

export interface Connection {
  /** The plugin's name. */
  readonly plugin: string;
  /** The routes its manifest lists, as "GET /api/v1/environments". */
  readonly routes: readonly string[];
  readonly apiVersion: ApiVersion;
  readonly environment: PluginEnvironment;
  readonly project: PluginProject | null;
  readonly theme: Theme;
  readonly api: Client;
  /** Follow a change; returns a function that stops following. */
  on<E extends keyof Events>(event: E, handler: (value: Events[E]) => void): () => void;
}

export interface ConnectOptions {
  /** How long to wait for YAAP to answer the handshake; 10 seconds by default. */
  timeoutMs?: number;
  /** How long to wait for the answer to an API call; 30 seconds by default. */
  callTimeoutMs?: number;
  /** Put the theme tokens on the page's root element and keep them current; true by default. */
  applyTheme?: boolean;
}

/** Connect to the YAAP page that shows this one. Rejects when the page is not shown by YAAP. */
export declare function connect(options?: ConnectOptions): Promise<Connection>;

/** A client that sends its calls to the YAAP page around this frame (window.parent by default). */
export declare function createClient(options?: { host?: Window; timeoutMs?: number }): Client;

/** Put theme tokens on an element (the page's root by default) as CSS custom properties. */
export declare function applyTheme(theme: Theme, root?: HTMLElement): void;
