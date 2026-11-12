import { useCallback, useEffect, useRef, useState } from "react";

import type { RouteStopOut } from "../../api/client";
import { removeStop, upsertStop } from "./routeStages";

export interface RouteStages {
  /** The stops of the reply that streams in, in the order they first arrived. */
  stops: RouteStopOut[];
  /** Add a stop, or replace the earlier stop with the same id. */
  upsert: (stop: RouteStopOut) => void;
  /** Drop a stop that is gone (a waiting stop whose question was answered). */
  remove: (id: string) => void;
}

/**
 * The route of the reply being followed, stop by stop. It starts empty with every reply and conversation, is dropped
 * when the reply ends, and a stop that arrives while no reply is followed is ignored.
 */
export function useRouteStages(following: boolean, conversationId: number | null): RouteStages {
  const [stops, setStops] = useState<RouteStopOut[]>([]);
  const followingNow = useRef(following);
  followingNow.current = following;

  useEffect(() => {
    setStops([]);
  }, [following, conversationId]);

  const upsert = useCallback((stop: RouteStopOut) => {
    if (followingNow.current) setStops((current) => upsertStop(current, stop));
  }, []);
  const remove = useCallback((id: string) => setStops((current) => removeStop(current, id)), []);

  return { stops, upsert, remove };
}
