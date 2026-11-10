import { useReloadNotice } from "./useReloadNotice";

/** Shown when YAAP was updated while this page stayed open, so an old page never talks to a new API unawares. */
export default function ReloadBar() {
  const version = useReloadNotice();
  if (!version) return null;
  return (
    <div className="reload-bar" role="status">
      <span>YAAP was updated to v{version}.</span>
      <button type="button" className="button" onClick={() => window.location.reload()}>
        Reload
      </button>
    </div>
  );
}
