import { useState } from "react";

import type { CliName } from "../../api/signIn";
import { useCliSignIn } from "./useCliSignIn";

const LABELS: Record<CliName, { button: string; vendor: string; name: string }> = {
  "claude-code": { button: "Sign in with your Claude subscription", vendor: "Claude", name: "Claude Code" },
  codex: { button: "Sign in with your ChatGPT plan", vendor: "ChatGPT", name: "Codex" },
};

/** The one-time code of a device sign-in, with a button that copies it. */
function UserCode({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // The browser refused the clipboard (no permission, or not a secure page): the code stays selectable.
      setCopied(false);
    }
  }

  return (
    <span className="cli-sign-in-code">
      <code className="mono">{code}</code>
      <button type="button" className="button button-ghost small" onClick={() => void copy()}>
        {copied ? "Copied" : "Copy"}
      </button>
    </span>
  );
}

function PasteCode({ busy, sent, onSubmit }: { busy: boolean; sent: boolean; onSubmit: (code: string) => void }) {
  const [code, setCode] = useState("");

  return (
    <form
      className="cli-sign-in-paste"
      onSubmit={(event) => {
        event.preventDefault();
        if (code.trim()) onSubmit(code);
      }}
    >
      <input
        className="mono"
        value={code}
        autoComplete="off"
        spellCheck={false}
        placeholder="Paste the code from the sign-in page"
        aria-label="Sign-in code"
        disabled={sent}
        onChange={(event) => setCode(event.target.value)}
      />
      <button type="submit" className="button button-primary small" disabled={busy || sent || !code.trim()}>
        {sent ? "Checking…" : "Submit"}
      </button>
    </form>
  );
}

/**
 * Signs in to Claude Code or Codex from the browser: a button, then the link to the vendor and either a field for the
 * code the vendor shows (Claude) or the one-time code to enter there (ChatGPT). The terminal command stays as a
 * fallback. `onSignedIn` refreshes the status shown next to it.
 */
export default function CliSignIn({
  cli,
  command,
  onSignedIn,
}: {
  cli: CliName;
  command: string;
  onSignedIn: () => void;
}) {
  const { signIn, error, busy, running, start, sendCode, cancel } = useCliSignIn(cli, onSignedIn);
  const labels = LABELS[cli];
  const state = signIn?.state ?? "idle";

  return (
    <div className="cli-sign-in">
      {!running && (
        <button type="button" className="button button-primary" disabled={busy} onClick={() => void start()}>
          {state === "failed" || state === "cancelled" ? "Try again" : labels.button}
        </button>
      )}
      {state === "signed_in" && <p className="form-ok small">Signed in.</p>}
      {state === "failed" && <p className="form-error small">{signIn?.message ?? "The sign-in did not work."}</p>}
      {error && <p className="form-error small">{error}</p>}
      {state === "installing" && (
        <p className="settings-help">
          Installing {labels.name}… (once, about 700 MB for Claude Code and Codex together, a few minutes)
        </p>
      )}
      {state === "starting" && <p className="settings-help">Starting the sign-in…</p>}
      {signIn?.url && running && (
        <ol className="cli-sign-in-steps">
          <li>
            <a href={signIn.url} target="_blank" rel="noreferrer noopener">
              Open the {labels.vendor} sign-in page
            </a>{" "}
            in a new tab and sign in.
          </li>
          {state === "waiting_for_code" && (
            <li>
              Copy the code it shows and paste it here.
              {signIn.message && <p className="form-error small">{signIn.message}</p>}
              <PasteCode busy={busy} sent={signIn.code_sent} onSubmit={(code) => void sendCode(code)} />
            </li>
          )}
          {state === "waiting_for_browser" && signIn.user_code && (
            <li>
              Enter this code there: <UserCode code={signIn.user_code} />
              <p className="settings-help">Waiting for you to finish in the browser…</p>
            </li>
          )}
        </ol>
      )}
      {running && (
        <button type="button" className="button button-ghost small" disabled={busy} onClick={() => void cancel()}>
          Cancel
        </button>
      )}
      <p className="settings-help">
        Without the browser: run <code className="mono">{command}</code> in a terminal in the YAAP folder.
      </p>
    </div>
  );
}
