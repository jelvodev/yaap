import { api, errorMessage } from "../api/client";
import type { components } from "../api/schema";

/** Where a recording is transcribed: the reply language of this level picks the model and language. */
export interface SpeechScope {
  environmentId: number;
  projectId: number | null;
}

export type SpeechStatus = components["schemas"]["SpeechStatusOut"];
export type Transcription = components["schemas"]["TranscriptionOut"];

function query(scope: SpeechScope) {
  return { path: { environment_id: scope.environmentId }, query: { project_id: scope.projectId ?? undefined } };
}

export async function speechStatus(scope: SpeechScope): Promise<SpeechStatus> {
  const { data, error } = await api.GET("/api/v1/environments/{environment_id}/speech", { params: query(scope) });
  if (!data) throw new Error(errorMessage(error, "The speech model's state could not be read."));
  return data;
}

export async function downloadSpeechModel(scope: SpeechScope): Promise<SpeechStatus> {
  const { data, error } = await api.POST("/api/v1/environments/{environment_id}/speech/download", {
    params: query(scope),
  });
  if (!data) throw new Error(errorMessage(error, "The speech model could not be downloaded."));
  return data;
}

/** Thrown when the model is not there yet: transcribing started its download. */
export class ModelNotReady extends Error {}

export async function transcribe(scope: SpeechScope, recording: Blob): Promise<Transcription> {
  const form = new FormData();
  const extension = recording.type.includes("ogg") ? "ogg" : recording.type.includes("mp4") ? "m4a" : "webm";
  form.append("audio", recording, `recording.${extension}`);
  const project = scope.projectId === null ? "" : `?project_id=${scope.projectId}`;
  const response = await fetch(`/api/v1/environments/${scope.environmentId}/speech/transcriptions${project}`, {
    method: "POST",
    body: form,
  });
  const body = await response.json().catch(() => null);
  if (response.status === 409) throw new ModelNotReady(errorMessage(body, "The speech model is being downloaded."));
  if (!response.ok) throw new Error(errorMessage(body, `The recording could not be transcribed (HTTP ${response.status}).`));
  return body as Transcription;
}

/** A container the browser can record and YAAP can read: WebM or Ogg with Opus, or MP4 (Safari). */
export function recordingType(): string | undefined {
  if (typeof MediaRecorder === "undefined") return undefined;
  return ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/webm", "audio/mp4"].find((type) =>
    MediaRecorder.isTypeSupported(type),
  );
}

export function megabytes(bytes: number): string {
  return `${Math.round(bytes / 1_000_000)} MB`;
}
