function TargetIcon({ size = 16 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="5" />
      <circle cx="12" cy="12" r="1" />
    </svg>
  );
}

/**
 * Goal mode (plan-chat-interface 2.2): starts `/goal ` in the message box, so the objective is typed after it and the
 * goal starts as a typed /goal does. Shown where /goal is offered (in a project); its title carries the estimate.
 */
export default function GoalButton({ summary, onClick }: { summary: string; onClick: () => void }) {
  return (
    <button type="button" className="attach-button" aria-label="Goal mode" title={`Goal mode: ${summary}`} onClick={onClick}>
      <TargetIcon size={16} />
    </button>
  );
}
