import { useCallback, useEffect, useRef, useState } from "react";

import type { Message } from "../../api/client";
import { SecondOpinionIcon } from "../icons";
import { useDismiss } from "../useDismiss";
import type { MessageActions } from "./MessageActions";
import { checkedLine, estimateLine, reviewerLine, sameVendorLine, type SecondOpinionPreview } from "./secondOpinionText";
import "./secondOpinion.css";

type State =
  | { step: "closed" }
  | { step: "loading" }
  | { step: "ready"; preview: SecondOpinionPreview }
  | { step: "asking"; preview: SecondOpinionPreview }
  | { step: "error"; message: string };

/** What asking would do: who reviews, what is checked and what it costs, before anything runs. */
function PreviewCard({
  preview,
  asking,
  error,
  onAsk,
  onCancel,
}: {
  preview: SecondOpinionPreview;
  asking: boolean;
  error: string | null;
  onAsk: () => void;
  onCancel: () => void;
}) {
  const sameVendor = sameVendorLine(preview);
  return (
    <div className="second-opinion-card" role="dialog" aria-label="Second opinion">
      {preview.available ? (
        <>
          <p>
            <strong>{reviewerLine(preview)}</strong>
          </p>
          {sameVendor && <p className="second-opinion-warning">{sameVendor}</p>}
          <p>{checkedLine(preview.kind)}</p>
          <p className="faint small">Cost: {estimateLine(preview.estimate)}</p>
        </>
      ) : (
        <p className="second-opinion-warning">{preview.reason ?? "No other model can give a second opinion."}</p>
      )}
      {error && <p className="second-opinion-warning">{error}</p>}
      <div className="second-opinion-buttons">
        {preview.available && (
          <button type="button" className="button" disabled={asking} onClick={onAsk}>
            {asking ? "Asking…" : "Ask"}
          </button>
        )}
        <button type="button" className="button button-ghost" onClick={onCancel}>
          {preview.available ? "Cancel" : "Close"}
        </button>
      </div>
    </div>
  );
}

/** The button under a reply that asks another model for a second opinion, after showing what it would do. */
export function SecondOpinionAction({ message, actions }: { message: Message; actions: MessageActions }) {
  const [state, setState] = useState<State>({ step: "closed" });
  const [error, setError] = useState<string | null>(null);
  const source = actions.secondOpinions;
  const anchor = useRef<HTMLSpanElement | null>(null);
  const close = useCallback(() => setState({ step: "closed" }), []);
  useDismiss(anchor, state.step !== "closed" && state.step !== "asking", close);
  // A card opened in one conversation does not follow into another.
  const conversationId = source?.conversationId;
  useEffect(() => {
    setState({ step: "closed" });
    setError(null);
  }, [conversationId]);
  if (!source) return null;

  async function open() {
    if (!source) return;
    setError(null);
    setState({ step: "loading" });
    const result = await source.preview(message.id);
    setState(result.preview ? { step: "ready", preview: result.preview } : { step: "error", message: result.error ?? "" });
  }

  async function ask(preview: SecondOpinionPreview) {
    if (!source) return;
    setState({ step: "asking", preview });
    const result = await source.start(message.id, preview.same_vendor);
    if (result.error) {
      setError(result.error);
      setState({ step: "ready", preview });
    } else {
      setState({ step: "closed" });
    }
  }

  const card = state.step === "ready" || state.step === "asking" ? state.preview : null;
  return (
    <span className="second-opinion-anchor" ref={anchor}>
      <button
        type="button"
        className="message-action-labelled"
        title="Ask another model to check this reply"
        disabled={state.step === "loading"}
        onClick={() => void open()}
      >
        <SecondOpinionIcon size={13} />
        Second opinion
      </button>
      {state.step === "error" && (
        <div className="second-opinion-card" role="alert">
          <p className="second-opinion-warning">{state.message}</p>
          <div className="second-opinion-buttons">
            <button type="button" className="button button-ghost" onClick={() => setState({ step: "closed" })}>
              Close
            </button>
          </div>
        </div>
      )}
      {card && (
        <PreviewCard
          preview={card}
          asking={state.step === "asking"}
          error={error}
          onAsk={() => void ask(card)}
          onCancel={() => setState({ step: "closed" })}
        />
      )}
    </span>
  );
}
