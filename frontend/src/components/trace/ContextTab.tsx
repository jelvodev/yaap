import ContextPanel, { type StepLink } from "../context/ContextPanel";
import { useContextBreakdown } from "../context/useContextBreakdown";

/** What the conversation holds in its context now (its latest call), with a link from each tool item to its step. */
export default function ContextTab({
  conversationId,
  onOpenStep,
}: {
  conversationId: number;
  onOpenStep: (link: StepLink) => void;
}) {
  const { breakdown, error } = useContextBreakdown(conversationId, null);
  if (error) return <p className="notice notice-error trace-pad">{error}</p>;
  if (!breakdown) return <p className="faint trace-pad">Loading…</p>;
  return (
    <div className="trace-tab-body">
      <p className="faint small">This is what the conversation holds now (its latest call), not only this reply.</p>
      <ContextPanel breakdown={breakdown} onOpenStep={onOpenStep} />
    </div>
  );
}
