import { type ReactNode, createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

import "./confirm-dialog.css";

/** What a confirmation asks. A bare string is the message with the default buttons. */
export type ConfirmRequest = {
  message: string;
  title?: string;
  /** The button that goes ahead; "OK" when absent. */
  confirmLabel?: string;
  /** The button that stops; "Cancel" when absent, and no second button when null (a notice). */
  cancelLabel?: string | null;
  /** "danger" for something that removes or cannot be undone. */
  tone?: "default" | "danger";
};

type Confirm = (request: ConfirmRequest | string) => Promise<boolean>;

type Pending = ConfirmRequest & { id: number; resolve: (answer: boolean) => void };

const ConfirmContext = createContext<Confirm | null>(null);

/**
 * Asks the user in YAAP's own dialog instead of the browser's `window.confirm`, which shows the page's address
 * as its title and cannot be styled. Resolves to true when the user goes ahead.
 */
export function useConfirm(): Confirm {
  const confirm = useContext(ConfirmContext);
  if (!confirm) throw new Error("useConfirm needs a ConfirmProvider around the app.");
  return confirm;
}

/** Shows one confirmation at a time; a second request while one is open waits its turn. */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [queue, setQueue] = useState<Pending[]>([]);
  const nextId = useRef(0);

  const confirm = useCallback<Confirm>((request) => {
    const asked = typeof request === "string" ? { message: request } : request;
    nextId.current += 1;
    const id = nextId.current;
    return new Promise<boolean>((resolve) => setQueue((current) => [...current, { ...asked, id, resolve }]));
  }, []);

  const pending = queue[0];
  const answer = useCallback(
    (value: boolean) => {
      if (!pending) return;
      pending.resolve(value);
      setQueue((current) => current.filter((item) => item.id !== pending.id));
    },
    [pending],
  );

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {pending && <ConfirmDialog key={pending.id} request={pending} onAnswer={answer} />}
    </ConfirmContext.Provider>
  );
}

function ConfirmDialog({ request, onAnswer }: { request: ConfirmRequest; onAnswer: (value: boolean) => void }) {
  const confirmRef = useRef<HTMLButtonElement>(null);
  const cancelLabel = request.cancelLabel === undefined ? "Cancel" : request.cancelLabel;
  const notice = cancelLabel === null;

  useEffect(() => {
    // Focus the safe choice for something destructive, so Enter does not delete by accident.
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (request.tone === "danger" && !notice) document.getElementById("confirm-dialog-cancel")?.focus();
    else confirmRef.current?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onAnswer(notice);
      }
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [request, notice, onAnswer]);

  return (
    <div className="palette-backdrop confirm-backdrop" onMouseDown={() => onAnswer(notice)}>
      <div
        className="palette confirm-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={request.title ? "confirm-dialog-title" : undefined}
        aria-label={request.title ? undefined : "Confirm"}
        aria-describedby="confirm-dialog-message"
        onMouseDown={(event) => event.stopPropagation()}
      >
        {request.title && <h2 id="confirm-dialog-title">{request.title}</h2>}
        <p id="confirm-dialog-message" className="confirm-dialog-message">
          {request.message}
        </p>
        <div className="confirm-dialog-actions">
          {cancelLabel !== null && (
            <button id="confirm-dialog-cancel" type="button" className="button button-ghost" onClick={() => onAnswer(false)}>
              {cancelLabel}
            </button>
          )}
          <button
            ref={confirmRef}
            type="button"
            className={request.tone === "danger" ? "button button-danger" : "button button-primary"}
            onClick={() => onAnswer(true)}
          >
            {request.confirmLabel ?? "OK"}
          </button>
        </div>
      </div>
    </div>
  );
}
