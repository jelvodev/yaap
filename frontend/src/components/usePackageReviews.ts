import { useCallback, useState } from "react";

import { api, errorMessage, type MutationStatus } from "../api/client";
import type { components } from "../api/schema";

export type PackageItem = components["schemas"]["PackageItemOut"];
export type PackagePreview = components["schemas"]["PackagePreviewOut"];
export type LevelQuery = { environment_id?: number; project_id?: number };

/** Whether an item of a package is installed, and under which name. */
export interface ItemChoice {
  chosen: boolean;
  name: string;
}

/** One package read for review: its link, what it holds at one commit, and the choice per item. */
export interface PackageReview {
  url: string;
  preview: PackagePreview;
  choices: Record<string, ItemChoice>;
}

const ITEM_NAME = /^[a-z0-9][a-z0-9-]{0,59}$/;

export function choiceKey(item: PackageItem): string {
  return `${item.kind}:${item.path}`;
}

/** Where an item lands in the Blueprint layer under a name. */
export function targetFor(kind: PackageItem["kind"], name: string): string {
  const folder = { standard: "standards", engineering: "engineering", command: "commands", agent: "agents" } as const;
  return kind === "skill" ? `skills/${name}/SKILL.md` : `${folder[kind]}/${name}.md`;
}

function nameOf(review: PackageReview, item: PackageItem): string {
  return review.choices[choiceKey(item)]?.name ?? item.name;
}

/** The items of a review that will be installed: chosen, and not refused. */
export function chosenItems(review: PackageReview): PackageItem[] {
  return review.preview.items.filter((item) => !item.error && review.choices[choiceKey(item)]?.chosen);
}

/** What stops an install of these reviews: a name that is not valid, or two items for one file; null when none. */
export function reviewProblem(reviews: PackageReview[]): string | null {
  const chosen = reviews.flatMap((review) => chosenItems(review).map((item) => ({ item, name: nameOf(review, item) })));
  const invalid = chosen.find(({ name }) => !ITEM_NAME.test(name));
  if (invalid) return `${invalid.name || "An empty name"}: use lowercase letters, digits and hyphens.`;
  const targets = chosen.map(({ item, name }) => targetFor(item.kind, name));
  const duplicate = targets.find((target, index) => targets.indexOf(target) !== index);
  return duplicate ? `Two items would be written to ${duplicate}; rename one.` : null;
}

function initialChoices(preview: PackagePreview, before: PackageReview | undefined): Record<string, ItemChoice> {
  return Object.fromEntries(
    preview.items.map((item) => [
      choiceKey(item),
      before?.choices[choiceKey(item)] ?? { chosen: !item.error && !item.exists, name: item.name },
    ]),
  );
}

/**
 * Packages read from GitHub for review before they are installed into a level: one for a pasted link, several for
 * the packs of YAAP's starter. Nothing is written until `install`.
 */
export function usePackageReviews() {
  const [reviews, setReviews] = useState<PackageReview[]>([]);
  const [busy, setBusy] = useState<"reading" | "installing" | null>(null);
  const [error, setError] = useState<string | null>(null);

  /** Read each link as seen from a level; the choices made before are kept unless `fresh`. */
  const read = useCallback(async (urls: string[], query: LevelQuery, fresh: boolean): Promise<boolean> => {
    setBusy("reading");
    setError(null);
    const results = await Promise.all(
      urls.map(async (url) => {
        const { data, error: failure } = await api.POST("/api/v1/blueprint/packages/preview", {
          params: { query },
          body: { url },
        });
        return { url, data, failure };
      }),
    );
    setBusy(null);
    const previews: { url: string; preview: PackagePreview }[] = [];
    for (const result of results) {
      if (!result.data) {
        setReviews([]);
        setError(errorMessage(result.failure, "The link could not be read."));
        return false;
      }
      previews.push({ url: result.url, preview: result.data });
    }
    setReviews((current) =>
      previews.map(({ url, preview }) => ({
        url,
        preview,
        choices: initialChoices(preview, fresh ? undefined : current.find((review) => review.url === url)),
      })),
    );
    return true;
  }, []);

  const choose = useCallback((url: string, key: string, change: Partial<ItemChoice>) => {
    setReviews((current) =>
      current.map((review) =>
        review.url === url && review.choices[key]
          ? { ...review, choices: { ...review.choices, [key]: { ...review.choices[key], ...change } } }
          : review,
      ),
    );
  }, []);

  /** Install the chosen items of every review, one package after the other; returns the paths written. */
  async function install(query: LevelQuery): Promise<{ paths: string[]; complete: boolean }> {
    setBusy("installing");
    setError(null);
    const paths: string[] = [];
    for (const review of reviews) {
      const items = chosenItems(review);
      if (items.length === 0) continue;
      const { data, error: failure } = await api.POST("/api/v1/blueprint/packages/install", {
        params: { query },
        body: {
          url: review.url,
          ref: review.preview.source.ref,
          commit: review.preview.source.commit,
          items: items.map((item) => ({ kind: item.kind, path: item.path, name: nameOf(review, item) })),
        },
      });
      if (!data) {
        setBusy(null);
        const fallback = paths.length
          ? `${paths.length} items were installed; ${review.preview.package.name} could not be.`
          : "The items could not be installed.";
        setError(errorMessage(failure, fallback));
        return { paths, complete: false };
      }
      paths.push(...data.items);
      if (!data.commit.committed) {
        setBusy(null);
        setError(commitFailure(data.commit));
        return { paths, complete: false };
      }
    }
    setBusy(null);
    return { paths, complete: true };
  }

  const clear = useCallback(() => {
    setReviews([]);
    setError(null);
  }, []);

  const chosenCount = reviews.reduce((count, review) => count + chosenItems(review).length, 0);
  return { reviews, busy, error, chosenCount, problem: reviewProblem(reviews), read, choose, install, clear };
}

function commitFailure(status: MutationStatus): string {
  return `Saved, but not committed: ${status.error ?? status.path}. Re-apply the package to retry.`;
}

export type PackageReviews = ReturnType<typeof usePackageReviews>;
