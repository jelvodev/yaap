import { type ReactNode, useContext, useMemo } from "react";

import type { Message } from "../../api/client";
import { callWhyFor } from "./behindChips";
import { BehindContext, CallReasonContext, OutputContext } from "./contexts";
import { useBehind } from "./useBehind";

interface LoadedBehindProps {
  conversationId: number;
  message: Message;
  children: ReactNode;
}

function LoadedBehind({ conversationId, message, children }: LoadedBehindProps) {
  const { behind } = useBehind(conversationId, message.id);
  const reasonFor = useMemo(() => (key: string) => callWhyFor(behind?.calls ?? [], key), [behind]);
  return (
    <BehindContext.Provider value={behind}>
      <CallReasonContext.Provider value={reasonFor}>{children}</CallReasonContext.Provider>
    </BehindContext.Provider>
  );
}

/** Loads what happened behind a stored reply once and gives it to the chips and the reply's tool calls. */
export function BehindProvider({ message, children }: { message: Message; children: ReactNode }) {
  const conversationId = useContext(OutputContext);
  if (conversationId === null) return <>{children}</>;
  return (
    <LoadedBehind conversationId={conversationId} message={message}>
      {children}
    </LoadedBehind>
  );
}
