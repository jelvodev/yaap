import { useEffect, useState } from "react";

import { useEnvironment } from "../environment/EnvironmentProvider";
import { type PackageLevel, PackageReviewList, PackageUpdateReview } from "./BlueprintPackages";
import { usePackageReviews } from "./usePackageReviews";
import { type StarterPack, useStarter } from "./useStarter";
import { Badge } from "./workbench/Workbench";
import "./starter-panel.css";

function packState(pack: StarterPack): { tone: "ok" | "warn"; text: string } | null {
  if (pack.outdated) return { tone: "warn", text: `installed from ${pack.installed_ref ?? "an older version"}` };
  if (pack.installed) return { tone: "ok", text: "installed" };
  return null;
}

/** One pack as a card: chosen or not, what it holds, and its state at the level. */
function PackCard({
  pack,
  chosen,
  updateLabel,
  onChoose,
  onUpdate,
}: {
  pack: StarterPack;
  chosen: boolean;
  updateLabel: string;
  onChoose: (chosen: boolean) => void;
  onUpdate: () => void;
}) {
  const state = packState(pack);
  return (
    <li className={chosen || pack.installed ? "starter-pack" : "starter-pack starter-pack-off"}>
      <label className="starter-pack-head">
        {!pack.installed && (
          <input type="checkbox" checked={chosen} onChange={(event) => onChoose(event.target.checked)} />
        )}
        <strong>{pack.title}</strong>
        {state && <Badge tone={state.tone}>{state.text}</Badge>}
      </label>
      <p className="small">{pack.description}</p>
      {pack.outdated && (
        <div>
          <button type="button" className="button small" onClick={onUpdate}>
            {updateLabel}
          </button>
        </div>
      )}
    </li>
  );
}

/**
 * YAAP's starter Blueprint: choose a level (Global by default) and the packs, review every item, then install.
 * A pack installed from an older version moves to this one through the usual update review.
 */
export default function StarterPanel({
  onInstalled,
}: {
  onInstalled?: (paths: string[], levelName: string) => void;
}) {
  const { environments } = useEnvironment();
  const levels: PackageLevel[] = [
    { key: "global", name: "Global", query: {} },
    ...environments.map((environment) => ({
      key: `environment-${environment.id}`,
      name: environment.name,
      query: { environment_id: environment.id },
    })),
  ];
  const [levelKey, setLevelKey] = useState("global");
  const level = levels.find((entry) => entry.key === levelKey) ?? levels[0];
  const { starter, error, reload } = useStarter(level.query, level.key);
  const review = usePackageReviews();
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [updating, setUpdating] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const { clear } = review;

  // A fresh look at the level: core and the other chosen packs that it lacks, nothing under review.
  useEffect(() => {
    setPicked(Object.fromEntries((starter?.packs ?? []).map((pack) => [pack.key, pack.chosen && !pack.installed])));
    clear();
  }, [starter, clear]);

  const available = (starter?.packs ?? []).filter((pack) => !pack.installed);
  const chosen = available.filter((pack) => picked[pack.key]);
  const version = starter ? (starter.pinned_to_version ? starter.version : starter.ref) : "";

  async function install() {
    const { paths, complete } = await review.install(level.query);
    if (paths.length > 0) setNotice(`Installed ${paths.length} item${paths.length === 1 ? "" : "s"} in ${level.name}.`);
    if (complete) onInstalled?.(paths, level.name);
    await reload();
  }

  return (
    <div className="starter-panel">
      <div className="form-row">
        <label htmlFor="starter-level" className="small">
          Install into
        </label>
        <select
          id="starter-level"
          value={level.key}
          onChange={(event) => {
            setLevelKey(event.target.value);
            setUpdating(null);
            setNotice(null);
          }}
        >
          {levels.map((entry) => (
            <option key={entry.key} value={entry.key}>
              {entry.name}
            </option>
          ))}
        </select>
        {starter && (
          <a href={starter.credits_url} target="_blank" rel="noreferrer" className="small">
            Credits
          </a>
        )}
      </div>
      <p className="muted small">
        Global serves every environment. Choose an environment to keep the starter to that one.
      </p>
      {error && <p className="form-error">{error}</p>}
      {!starter && !error && <p className="muted small">Reading the starter from GitHub…</p>}
      {starter && (
        <>
          <p className="faint small">
            {starter.repository} · {starter.ref}
            {!starter.pinned_to_version && ` · not pinned to version ${starter.version}; read from ${starter.ref}`}
          </p>
          <ul className="starter-packs">
            {starter.packs.map((pack) => (
              <PackCard
                key={pack.key}
                pack={pack}
                chosen={Boolean(picked[pack.key])}
                updateLabel={`Update to ${version}`}
                onChoose={(value) => {
                  setPicked((current) => ({ ...current, [pack.key]: value }));
                  clear();
                }}
                onUpdate={() => setUpdating(pack.name)}
              />
            ))}
          </ul>
          {updating && (
            <PackageUpdateReview
              key={`${level.key}-${updating}`}
              name={updating}
              query={level.query}
              updateRef={starter.ref}
              onClose={() => setUpdating(null)}
              onUpdated={async (paths) => {
                setUpdating(null);
                setNotice(`Updated ${paths.length} item${paths.length === 1 ? "" : "s"} in ${level.name}.`);
                onInstalled?.(paths, level.name);
                await reload();
              }}
            />
          )}
          {notice && <p className="form-ok small">{notice}</p>}
          {available.length === 0 ? (
            <p className="muted small">Every pack is installed in {level.name}.</p>
          ) : (
            review.reviews.length === 0 && (
              <div className="form-row">
                <button
                  type="button"
                  className="button button-primary"
                  disabled={review.busy !== null || chosen.length === 0}
                  onClick={() => void review.read(chosen.map((pack) => pack.url), level.query, true)}
                >
                  {review.busy === "reading"
                    ? "Reading…"
                    : `Review ${chosen.length} pack${chosen.length === 1 ? "" : "s"}`}
                </button>
                <span className="faint small">Nothing is written before you install.</span>
              </div>
            )
          )}
          {review.error && <p className="form-error">{review.error}</p>}
          {review.reviews.map((entry) => (
            <section key={entry.url} className="starter-review">
              <PackageReviewList review={entry} onChoose={review.choose} />
            </section>
          ))}
          {review.reviews.length > 0 && (
            <>
              {review.problem && <p className="form-error">{review.problem}</p>}
              <div className="form-row">
                <button
                  type="button"
                  className="button button-primary"
                  disabled={review.busy !== null || review.chosenCount === 0 || review.problem !== null}
                  onClick={() => void install()}
                >
                  {review.busy === "installing"
                    ? "Installing…"
                    : `Install ${review.chosenCount} item${review.chosenCount === 1 ? "" : "s"} in ${level.name}`}
                </button>
                <button type="button" className="button button-ghost" onClick={clear}>
                  Back to the packs
                </button>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
