import { Link } from "react-router-dom";

import { useContinuationLinks } from "./useReturnNotice";

/** "Continued from: …" and "Continued in: …" (the newest) beside the title; each opens that conversation. */
export default function ContinuationChip({ conversationId }: { conversationId: number }) {
  const { continuedFrom, continuedIn } = useContinuationLinks(conversationId);
  const newest = continuedIn.at(-1);
  return (
    <>
      {continuedFrom && (
        <Link className="chip" to={`/chat/${continuedFrom.id}`} title="The conversation this one continues">
          Continued from: {continuedFrom.title}
        </Link>
      )}
      {newest && (
        <Link className="chip" to={`/chat/${newest.id}`} title="The conversation that continues this one">
          Continued in: {newest.title}
        </Link>
      )}
    </>
  );
}
