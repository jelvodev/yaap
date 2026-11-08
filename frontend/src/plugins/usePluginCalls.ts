import { type RefObject, useEffect } from "react";

import { callForPlugin, type PluginCallResult } from "../api/plugin-calls";
import { callAddress, callRefusal, parseBridgeCall } from "./bridgeCheck";
import { hostMessage, isFrameMessage } from "./messages";

/** The plugin whose page the frame shows, and the routes its manifest lists. */
export interface PluginPage {
  name: string;
  routes: string[];
}

/**
 * The API calls a plugin page asks for. The SDK in the frame posts each call; YAAP answers only its own frame (the
 * frame's origin is opaque, so the source decides, not the origin), checks the call against the plugin's manifest,
 * makes it with the session and posts the outcome back under the call's id. The frame never reaches the API itself.
 */
export function usePluginCalls(frame: RefObject<HTMLIFrameElement | null>, page: PluginPage): void {
  useEffect(() => {
    function answer(target: Window, id: string, result: PluginCallResult) {
      target.postMessage(hostMessage("response", { id, ...result }), "*");
    }

    function onMessage(event: MessageEvent) {
      const target = frame.current?.contentWindow;
      if (!target || event.source !== target || !isFrameMessage(event.data, "request")) return;
      const parsed = parseBridgeCall(event.data);
      if (!parsed.ok) {
        if (parsed.id !== null) answer(target, parsed.id, { ok: false, status: 400, error: { detail: parsed.problem } });
        return;
      }
      const { call } = parsed;
      const refusal = callRefusal(page.name, page.routes, call);
      if (refusal !== null) {
        answer(target, call.id, { ok: false, status: 403, error: { detail: refusal } });
        return;
      }
      void callForPlugin(page.name, call.method, callAddress(call), call.body).then((result) =>
        answer(target, call.id, result),
      );
    }

    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [frame, page]);
}
