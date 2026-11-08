import { api, errorMessage } from "../api/client";
import { LIMITS, formatLimit } from "./contextSettings";

/**
 * The context limit of a conversation, next to effort: "settings" follows the context_limit setting,
 * a size limits this conversation. With a conversation id the choice is saved at once; without, it is a draft.
 */
export default function ContextLimitSelect({
  id,
  value,
  window: contextWindow,
  conversationId,
  disabled,
  className,
  plain = false,
  onChange,
  onError,
}: {
  id: string;
  value: number | null;
  window: number | null | undefined;
  conversationId?: number;
  disabled?: boolean;
  className?: string;
  // Beside a "Limit" label: the options without their "limit ·" prefix.
  plain?: boolean;
  onChange: (value: number | null) => void;
  onError?: (message: string) => void;
}) {
  const prefix = plain ? "" : "limit · ";
  // Only the sizes within the model's window are offered.
  const sizes = LIMITS.filter((size) => !contextWindow || size <= contextWindow);
  if (contextWindow && !sizes.includes(contextWindow)) sizes.push(contextWindow);
  if (value !== null && !sizes.includes(value)) sizes.push(value);
  sizes.sort((left, right) => left - right);

  async function choose(next: number | null) {
    if (conversationId === undefined) {
      onChange(next);
      return;
    }
    const { error: requestError } = await api.PATCH("/api/v1/conversations/{conversation_id}", {
      params: { path: { conversation_id: conversationId } },
      body: { context_limit: next },
    });
    if (requestError) {
      onError?.(errorMessage(requestError, "The context limit could not be changed."));
      return;
    }
    onChange(next);
  }

  return (
    <>
      <label className="visually-hidden" htmlFor={id}>
        Context limit
      </label>
      <select
        id={id}
        className={className}
        value={value === null ? "" : String(value)}
        disabled={disabled}
        title="How large this conversation's context may grow before YAAP shortens old tool output and summarises; the model keeps its full window"
        onChange={(event) => void choose(event.target.value ? Number(event.target.value) : null)}
      >
        <option value="">{prefix}settings</option>
        {sizes.map((size) => (
          <option key={size} value={size}>
            {prefix}
            {formatLimit(size)}
            {size === contextWindow ? " (window)" : ""}
          </option>
        ))}
      </select>
    </>
  );
}
