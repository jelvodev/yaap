import { useCallback, useEffect, useState } from "react";

import {
  downloadLocalModel,
  listLocalModels,
  readAutomaticDownload,
  removeLocalModel,
  saveAutomaticDownload,
} from "../api/models";
import { type LocalModel, anyDownloading } from "./modelState";

const POLL_MS = 2_000;

/** The downloadable models and their states, read again every two seconds while one is downloading. */
export function useLocalModels() {
  const [models, setModels] = useState<LocalModel[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setModels(await listLocalModels());
      setError(null);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "The models could not be loaded.");
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const downloading = models !== null && anyDownloading(models);
  useEffect(() => {
    if (!downloading) return;
    const timer = window.setInterval(() => void reload(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [downloading, reload]);

  const act = useCallback(
    async (run: () => Promise<LocalModel>) => {
      try {
        await run();
      } catch (failure) {
        setError(failure instanceof Error ? failure.message : "That did not work.");
        return;
      }
      await reload();
    },
    [reload],
  );

  const download = useCallback((name: string) => act(() => downloadLocalModel(name)), [act]);
  const remove = useCallback((name: string) => act(() => removeLocalModel(name)), [act]);

  return { models, error, download, remove };
}

/** The switch for downloading the search model by itself. */
export function useAutomaticDownload() {
  const [on, setOn] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    void readAutomaticDownload().then((value) => current && setOn(value));
    return () => {
      current = false;
    };
  }, []);

  const change = useCallback(async (value: boolean) => {
    setOn(value);
    try {
      await saveAutomaticDownload(value);
      setError(null);
    } catch (failure) {
      setOn(!value);
      setError(failure instanceof Error ? failure.message : "The setting could not be saved.");
    }
  }, []);

  return { on, error, change };
}
