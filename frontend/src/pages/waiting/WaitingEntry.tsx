import { NavLink } from "react-router-dom";

import { useWaitingCount } from "./useWaiting";
import "./waiting.css";

export const WAITING_PATH = "/chat/waiting";

/** The sidebar's entry at the top: Waiting for me, with how many rows wait (asked again every half minute). */
export default function WaitingEntry({ environmentId }: { environmentId: number }) {
  const count = useWaitingCount(environmentId);
  return (
    <NavLink to={WAITING_PATH} className={({ isActive }) => `waiting-entry${isActive ? " active" : ""}`}>
      Waiting for me
      {count !== null && count > 0 && <span className="waiting-count">{count}</span>}
    </NavLink>
  );
}
