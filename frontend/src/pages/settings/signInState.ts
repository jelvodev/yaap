import type { SignIn } from "../../api/signIn";

type State = SignIn["state"];

/** How often a running sign-in is asked for news. */
export const POLL_MILLISECONDS = 2000;

/** Whether a sign-in process is running and the page should keep asking for news. */
export function isRunning(state: State): boolean {
  return (
    state === "installing" ||
    state === "starting" ||
    state === "waiting_for_code" ||
    state === "waiting_for_browser"
  );
}

/**
 * The state a page opens with. A sign-in that ended before this page was opened is history, not news: only a running
 * one is picked up again (after a reload, say).
 */
export function openingState(state: SignIn | null): SignIn | null {
  return state && isRunning(state.state) ? state : null;
}

/** Whether the change from one state to the next is the moment the sign-in succeeded. */
export function justSignedIn(before: State | undefined, after: State): boolean {
  return after === "signed_in" && before !== undefined && before !== "signed_in";
}
