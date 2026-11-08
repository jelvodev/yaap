import { useMemo, useRef } from "react";

import { usePluginBridge } from "./usePluginBridge";
import { usePluginCalls } from "./usePluginCalls";

interface PluginFrameProps {
  name: string;
  url: string;
  routes: string[];
}

/**
 * A plugin's page in a sandboxed frame (an opaque origin), connected to YAAP through the plugin SDK: the handshake and
 * context, and the API calls YAAP makes for it.
 */
export default function PluginFrame({ name, url, routes }: PluginFrameProps) {
  const frame = useRef<HTMLIFrameElement>(null);
  const page = useMemo(() => ({ name, routes }), [name, routes]);
  usePluginBridge(frame, page);
  usePluginCalls(frame, page);
  return (
    <iframe
      ref={frame}
      className="page-preview-frame"
      title={`Page of the ${name} plugin`}
      sandbox="allow-scripts allow-modals"
      referrerPolicy="no-referrer"
      src={url}
    />
  );
}
