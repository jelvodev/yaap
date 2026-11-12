import { type KeyboardEvent, useCallback, useRef, useState } from "react";

import type { Why } from "../../api/client";
import { useDismiss } from "../useDismiss";
import "./why.css";

/** The reason itself: the rule, the setting that controls it and the layer that setting is set in. */
export function WhyText({ why }: { why: Why }) {
  return (
    <div className="why-text">
      <p>{why.rule}</p>
      {why.setting && <p className="why-meta">Setting: {why.setting}</p>}
      {why.layer && <p className="why-meta">Set in: {why.layer}</p>}
    </div>
  );
}

/** A small "why" button that opens the reason of a decision; Escape or a click outside closes it. */
export function WhyPopover({ why, label = "why" }: { why: Why; label?: string }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLSpanElement | null>(null);
  const button = useRef<HTMLButtonElement | null>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismiss(box, open, close);

  // Escape from the button or the popover closes it and puts the focus back on the button.
  function onKeyDown(event: KeyboardEvent) {
    if (event.key !== "Escape" || !open) return;
    event.stopPropagation();
    setOpen(false);
    button.current?.focus();
  }

  return (
    <span className="why" ref={box} onKeyDown={onKeyDown}>
      <button
        type="button"
        ref={button}
        className="why-button"
        aria-expanded={open}
        aria-haspopup="dialog"
        title="Why this happened"
        onClick={(event) => {
          event.stopPropagation();
          setOpen((value) => !value);
        }}
      >
        {label}
      </button>
      {open && (
        <div className="why-popover" role="dialog" aria-label="Why">
          <WhyText why={why} />
        </div>
      )}
    </span>
  );
}
