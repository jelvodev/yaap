import { type RefObject, useCallback, useEffect, useMemo, useRef, useState } from "react";

import protocol from "../../plugin-sdk/protocol.json";
import { api } from "../api/client";
import { useEnvironment } from "../environment/EnvironmentProvider";
import { readScope } from "../environment/scope";
import { hostMessage, isFrameMessage } from "./messages";
import type { PluginPage } from "./usePluginCalls";

interface BridgeTheme {
  scheme: "dark" | "light";
  tokens: Record<string, string>;
}

interface BridgeContext {
  environment: { id: number; name: string; colour: string };
  project: { id: number; name: string } | null;
}

/** YAAP's theme as the SDK puts it on the page: the current values of the tokens in protocol.json. */
function currentTheme(): BridgeTheme {
  const root = document.documentElement;
  const style = getComputedStyle(root);
  const tokens: Record<string, string> = {};
  for (const token of protocol.themeTokens) {
    const value = style.getPropertyValue(token).trim();
    if (value) tokens[token] = value;
  }
  return { scheme: root.dataset.theme === "light" ? "light" : "dark", tokens };
}

/** The project the chat shows in the active environment, as the plugin page's active project. */
function useActiveProject(environmentId: number | null): BridgeContext["project"] {
  const [project, setProject] = useState<BridgeContext["project"]>(null);
  useEffect(() => {
    const projectId = environmentId === null ? null : readScope(environmentId);
    if (environmentId === null || projectId === null) {
      setProject(null);
      return;
    }
    let cancelled = false;
    void api
      .GET("/api/v1/environments/{environment_id}/projects", { params: { path: { environment_id: environmentId } } })
      .then(({ data }) => {
        if (cancelled) return;
        const found = data?.find((item) => item.id === projectId);
        setProject(found ? { id: found.id, name: found.name } : null);
      });
    return () => {
      cancelled = true;
    };
  }, [environmentId]);
  return project;
}

/**
 * The YAAP side of the plugin SDK's handshake. The page in the frame asks with a hello; YAAP answers that frame
 * with the plugin's routes, the active environment and project and the theme, and sends their changes afterwards.
 * The frame is sandboxed without allow-same-origin, so its origin is opaque and messages to it go to "*"; only the
 * frame's own window is answered.
 */
export function usePluginBridge(frame: RefObject<HTMLIFrameElement | null>, page: PluginPage): void {
  const { active } = useEnvironment();
  const project = useActiveProject(active?.id ?? null);
  const context = useMemo<BridgeContext | null>(
    () => (active ? { environment: { id: active.id, name: active.name, colour: active.colour }, project } : null),
    [active, project],
  );
  const latest = useRef(context);
  latest.current = context;
  const connected = useRef(false);

  const post = useCallback((message: object) => frame.current?.contentWindow?.postMessage(message, "*"), [frame]);

  useEffect(() => {
    function answer(event: MessageEvent) {
      const target = frame.current?.contentWindow;
      if (!target || event.source !== target || !isFrameMessage(event.data, "hello")) return;
      // Until the environments have loaded there is nothing to tell; the SDK asks again.
      if (!latest.current) return;
      connected.current = true;
      const init = { plugin: page.name, routes: page.routes, ...latest.current, theme: currentTheme() };
      post(hostMessage("init", init));
    }
    window.addEventListener("message", answer);
    return () => window.removeEventListener("message", answer);
  }, [frame, page, post]);

  useEffect(() => {
    if (context && connected.current) post(hostMessage("context", context));
  }, [context, post]);

  useEffect(() => {
    // The theme lives on the root element: data-theme for dark or light, and the accent of the environment.
    const observer = new MutationObserver(() => {
      if (connected.current) post(hostMessage("theme", { theme: currentTheme() }));
    });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "style"] });
    return () => observer.disconnect();
  }, [post]);
}
