import { type PackageLevel, PackageInstallPanel } from "../../components/BlueprintPackages";
import StarterPanel from "../../components/StarterPanel";
import { PageHeader } from "../../components/workbench/Workbench";

export type PackageSource = "link" | "starter";

/** Blueprint › Add from a package: a package from a GitHub link, or YAAP's own starter. */
export default function PackagesDetail({
  source,
  levelName,
  levels,
  initialLevel,
  onSource,
  onClose,
  onInstalled,
}: {
  source: PackageSource;
  levelName: string;
  levels: PackageLevel[];
  initialLevel: string;
  onSource: (source: PackageSource) => void;
  onClose: () => void;
  onInstalled: (paths: string[], levelName: string) => void;
}) {
  return (
    <>
      <PageHeader
        crumb={`${levelName} › Packages`}
        title={source === "starter" ? "YAAP starter" : "Add from a package"}
        actions={
          source === "starter" ? (
            <>
              <button type="button" className="button" onClick={() => onSource("link")}>
                From a link
              </button>
              <button type="button" className="button button-ghost" onClick={onClose}>
                Close
              </button>
            </>
          ) : (
            <button type="button" className="button" onClick={() => onSource("starter")}>
              YAAP starter
            </button>
          )
        }
      />
      <div className="wb-body wb-body-narrow">
        {source === "starter" ? (
          <section className="card import-panel">
            <p className="muted small">
              YAAP&apos;s own starter Blueprint: standards, skills, commands and agents that give a good setup from the
              start. Core is the base; the other packs add a field of work.
            </p>
            <StarterPanel onInstalled={onInstalled} />
          </section>
        ) : (
          <PackageInstallPanel
            levels={levels}
            initialLevel={initialLevel}
            onClose={onClose}
            onInstalled={onInstalled}
          />
        )}
      </div>
    </>
  );
}
