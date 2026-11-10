import { useAutomaticDownload } from "./useLocalModels";

/** The switch for downloading the search model in the background once the first start is finished. */
export default function AutomaticDownloadSwitch({ label }: { label: string }) {
  const { on, error, change } = useAutomaticDownload();
  return (
    <>
      <label className="checkbox-row">
        <input
          type="checkbox"
          checked={on ?? true}
          disabled={on === null}
          onChange={(event) => void change(event.target.checked)}
        />{" "}
        {label}
      </label>
      {error && <p className="form-error settings-help">{error}</p>}
    </>
  );
}
