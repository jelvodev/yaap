import { Badge, Panel } from "../../components/workbench/Workbench";
import AutomaticDownloadSwitch from "../../models/AutomaticDownloadSwitch";
import { type LocalModel, percent, sizeText, stateText } from "../../models/modelState";
import { useLocalModels } from "../../models/useLocalModels";
import { type SettingsLevel, SectionHeader } from "./shared";

const TONES = { ready: "ok", downloading: "warn", failed: "danger", not_downloaded: "off" } as const;

function ModelRow({
  model,
  onDownload,
  onRemove,
}: {
  model: LocalModel;
  onDownload: () => void;
  onRemove: () => void;
}) {
  return (
    <li className="model-row">
      <div className="model-row-main">
        <strong>{model.label}</strong>
        <span className="muted small">
          {model.purpose} · {sizeText(model.size)}
        </span>
        {model.state === "downloading" && (
          <progress value={percent(model)} max={100} aria-label={`Download of ${model.label}`} />
        )}
        {model.state === "failed" && <span className="form-error small">{model.message}</span>}
      </div>
      <Badge tone={TONES[model.state]}>{stateText(model)}</Badge>
      {model.state === "ready" ? (
        <button type="button" className="button" onClick={onRemove}>
          Remove
        </button>
      ) : (
        <button type="button" className="button" disabled={model.state === "downloading"} onClick={onDownload}>
          {model.state === "failed" ? "Try again" : "Download"}
        </button>
      )}
    </li>
  );
}

/** Global only: the models YAAP downloads into its data folder, and whether the search model comes by itself. */
export default function ModelsSection({ level }: { level: SettingsLevel }) {
  const { models, error, download, remove } = useLocalModels();
  return (
    <>
      <SectionHeader
        level={level}
        title="Models"
        info={
          <p>
            Models that run inside YAAP, so nothing leaves your computer. They are downloaded once into the data folder
            and checked against their SHA-256. Backups leave them out.
          </p>
        }
      />
      <div className="wb-body wb-body-narrow wb-stack">
        <Panel title="Downloaded models" flush>
          {models ? (
            <ul className="model-list">
              {models.map((model) => (
                <ModelRow
                  key={model.name}
                  model={model}
                  onDownload={() => void download(model.name)}
                  onRemove={() => void remove(model.name)}
                />
              ))}
            </ul>
          ) : (
            <p className="muted small">Loading…</p>
          )}
          {error && <p className="form-error">{error}</p>}
        </Panel>
        <Panel title="Search by meaning">
          <AutomaticDownloadSwitch label="Download the search model by itself after the first start" />
          <p className="muted small">
            Until the search model is there, search finds words only; conversations and documents are indexed by
            meaning as soon as it arrives.
          </p>
        </Panel>
      </div>
    </>
  );
}
