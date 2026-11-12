import { useCallback, useContext, useRef, useState } from "react";

import type { BehindLabel, Message } from "../../api/client";
import RouteNote from "../RouteNote";
import { useDismiss } from "../useDismiss";
import { WhyText } from "../why/WhyPopover";
import { labelLines, splitChips } from "./behindChips";
import { BehindContext } from "./contexts";
import type { MessageActions } from "./MessageActions";
import "./behind.css";

function LabelPopover({ label, message, actions }: { label: BehindLabel; message: Message; actions?: MessageActions }) {
  const facts = labelLines(label);
  const showTrace = actions?.onShowTrace;
  return (
    <div className="behind-popover" role="dialog" aria-label={label.text}>
      {label.detail && <p>{label.detail}</p>}
      {label.items.length > 0 && (
        <ul>
          {label.items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      )}
      {facts.length > 0 && <p className="behind-facts">{facts.join(" · ")}</p>}
      {label.why && <WhyText why={label.why} />}
      {showTrace && label.run_id !== null && (
        <button type="button" className="link-button" onClick={() => showTrace(message)}>
          Show in trace
        </button>
      )}
    </div>
  );
}

/** The small labels under a stored reply, each opening to its reason and cost. A reply without labels (made before
 * they existed, or without a run) keeps the plain route note. */
export function BehindChips({ message, actions }: { message: Message; actions?: MessageActions }) {
  const behind = useContext(BehindContext);
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [showMore, setShowMore] = useState(false);
  const box = useRef<HTMLDivElement | null>(null);
  const close = useCallback(() => setOpenKey(null), []);
  useDismiss(box, openKey !== null, close);

  const labels = behind?.labels ?? [];
  if (labels.length === 0) return message.route ? <RouteNote route={message.route} /> : null;
  const { shown, more } = splitChips(labels);
  const visible = showMore ? [...shown, ...more] : shown;
  const chip = (label: BehindLabel, index: number) => {
    const key = `${label.kind}-${index}`;
    const open = openKey === key;
    return (
      <div className="behind-item" key={key}>
        <button
          type="button"
          className="behind-chip"
          aria-expanded={open}
          aria-haspopup="dialog"
          onClick={() => setOpenKey(open ? null : key)}
          onKeyDown={(event) => event.key === "Escape" && open && setOpenKey(null)}
        >
          {label.text}
        </button>
        {open && <LabelPopover label={label} message={message} actions={actions} />}
      </div>
    );
  };
  return (
    <div className="behind" ref={box}>
      {visible.map(chip)}
      {!showMore && more.length > 0 && (
        <button type="button" className="behind-chip" onClick={() => setShowMore(true)}>
          +{more.length} more
        </button>
      )}
    </div>
  );
}
