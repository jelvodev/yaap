// Type check of the generated declarations (npm run check:sdk): how a plugin written in TypeScript uses the SDK.
// Lines marked @ts-expect-error must fail; tsc reports an error when they stop failing.

import { type Connection, type Theme, connect } from "../.build/plugin-sdk.js";

export async function listProjects(yaap: Connection): Promise<string[]> {
  const { data, error } = await yaap.api.GET("/api/v1/environments/{environment_id}/projects", {
    params: { path: { environment_id: yaap.environment.id } },
  });
  if (error) throw new Error(String(error.detail));
  return data.map((project) => project.name);
}

export async function examples(): Promise<void> {
  const yaap = await connect({ timeoutMs: 5000 });
  const environments = await yaap.api.GET("/api/v1/environments");
  if (environments.data) environments.data.map((environment) => environment.name.toUpperCase());

  const found = await yaap.api.GET("/api/v1/environments/{environment_id}/search", {
    params: { path: { environment_id: 1 }, query: { q: "release", limit: 5 } },
  });
  void found.data;

  const saved = await yaap.api.PUT("/api/v1/settings/{key}", {
    params: { path: { key: "interface" } },
    body: { value: { theme: "light" } },
  });
  void saved.status;

  const stop = yaap.on("theme", (theme: Theme) => theme.tokens["--accent"]);
  stop();
  yaap.on("context", ({ environment, project }) => `${environment.name} ${project?.name ?? ""}`);

  // @ts-expect-error: no such route
  await yaap.api.GET("/api/v1/nothing-here");
  // @ts-expect-error: the path parameter is required
  await yaap.api.GET("/api/v1/environments/{environment_id}/projects");
  // @ts-expect-error: the search needs its query
  await yaap.api.GET("/api/v1/environments/{environment_id}/search", { params: { path: { environment_id: 1 } } });
  // @ts-expect-error: the environments list takes no POST body of this shape
  await yaap.api.POST("/api/v1/environments", { body: { colour: 3 } });
  // @ts-expect-error: unknown event
  yaap.on("message", () => undefined);
}
