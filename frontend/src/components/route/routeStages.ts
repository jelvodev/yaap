// Updating the stops of a streaming route (pure, tested with `npm run test:unit`). Only type imports.
import type { components } from "../../api/schema";

type Stop = components["schemas"]["RouteStopOut"];

/** Add a stop, or replace the earlier stop with the same id where it stood. */
export function upsertStop(stops: readonly Stop[], stop: Stop): Stop[] {
  return stops.some((entry) => entry.id === stop.id)
    ? stops.map((entry) => (entry.id === stop.id ? stop : entry))
    : [...stops, stop];
}

/** Drop the stop with this id; the others keep their order. */
export function removeStop(stops: readonly Stop[], id: string): Stop[] {
  return stops.filter((entry) => entry.id !== id);
}
