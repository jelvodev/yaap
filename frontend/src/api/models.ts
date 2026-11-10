import type { components } from "./schema";
import { api, errorMessage } from "./client";

export type LocalModel = components["schemas"]["LocalModelOut"];
export type SearchMeaning = components["schemas"]["SearchIndexStatusOut"]["meaning"];

const AUTOMATIC_KEY = "search_model_download";

export async function listLocalModels(): Promise<LocalModel[]> {
  const { data, error } = await api.GET("/api/v1/local-models");
  if (!data) throw new Error(errorMessage(error, "The models could not be loaded."));
  return data;
}

export async function downloadLocalModel(name: string): Promise<LocalModel> {
  const { data, error } = await api.POST("/api/v1/local-models/{name}/download", { params: { path: { name } } });
  if (!data) throw new Error(errorMessage(error, "The download could not be started."));
  return data;
}

export async function removeLocalModel(name: string): Promise<LocalModel> {
  const { data, error } = await api.DELETE("/api/v1/local-models/{name}", { params: { path: { name } } });
  if (!data) throw new Error(errorMessage(error, "The model could not be removed."));
  return data;
}

/** Whether the search model downloads by itself once the first start is finished (on unless switched off). */
export async function readAutomaticDownload(): Promise<boolean> {
  const { data } = await api.GET("/api/v1/settings");
  const setting = data?.find((item) => item.key === AUTOMATIC_KEY);
  return setting ? setting.value !== "off" : true;
}

export async function saveAutomaticDownload(on: boolean): Promise<void> {
  const { error } = await api.PUT("/api/v1/settings/{key}", {
    params: { path: { key: AUTOMATIC_KEY } },
    body: { value: on ? "on" : "off" },
  });
  if (error) throw new Error(errorMessage(error, "The setting could not be saved."));
}

/** Whether search by meaning works now: ready, off in Settings, or not ready yet (the model is not there). */
export async function searchMeaning(): Promise<SearchMeaning | null> {
  const { data } = await api.GET("/api/v1/search-index/status");
  return data?.meaning ?? null;
}
