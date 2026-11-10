import { useCallback, useEffect, useRef, useState } from "react";

import {
  type CliName,
  type SignIn,
  type SignInResult,
  cancelSignIn,
  readSignIn,
  sendSignInCode,
  startSignIn,
} from "../../api/signIn";
import { POLL_MILLISECONDS, isRunning, justSignedIn, openingState } from "./signInState";

/**
 * The state of signing in to a subscription CLI from the browser: start, paste the code, cancel, and the polling
 * that follows a running sign-in. `onSignedIn` is called once, when the sign-in succeeds.
 */
export function useCliSignIn(cli: CliName, onSignedIn: () => void) {
  const [signIn, setSignIn] = useState<SignIn | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const previous = useRef<SignIn["state"] | undefined>(undefined);
  const signedIn = useRef(onSignedIn);
  signedIn.current = onSignedIn;

  const adopt = useCallback((result: SignInResult) => {
    if (result.sign_in) {
      const next = result.sign_in;
      setError(null);
      setSignIn(next);
      if (justSignedIn(previous.current, next.state)) signedIn.current();
      previous.current = next.state;
    } else {
      setError(result.error);
    }
  }, []);

  useEffect(() => {
    let current = true;
    void readSignIn(cli).then((result) => {
      if (!current) return;
      const opened = openingState(result.sign_in ?? null);
      previous.current = opened?.state;
      setSignIn(opened);
    });
    return () => {
      current = false;
    };
  }, [cli]);

  const running = signIn !== null && isRunning(signIn.state);
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => void readSignIn(cli).then(adopt), POLL_MILLISECONDS);
    return () => window.clearInterval(timer);
  }, [running, cli, adopt]);

  async function act(call: () => Promise<SignInResult>) {
    setBusy(true);
    adopt(await call());
    setBusy(false);
  }

  return {
    signIn,
    error,
    busy,
    running,
    start: () => act(() => startSignIn(cli)),
    sendCode: (code: string) => act(() => sendSignInCode(cli, code)),
    cancel: () => act(() => cancelSignIn(cli)),
  };
}
