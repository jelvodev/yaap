import { useCallback, useEffect, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";
import { Badge, EmptyState, Panel } from "../../components/workbench/Workbench";
import PluginFrame from "../../plugins/PluginFrame";
import { type SettingsLevel, SectionHeader } from "./shared";

type Plugin = components["schemas"]["PluginOut"];

const RIGHTS_WARNING =
  "A plugin is Python code that runs inside YAAP with YAAP's rights: it can read and change everything in the data " +
  "folder, use the keys in the vault, run commands in the container and reach the network. Switch on only code you " +
  "wrote or read yourself.";

function status(plugin: Plugin): { tone: "ok" | "warn" | "danger" | "off"; label: string } {
  if (plugin.error) return { tone: "danger", label: "failed, switched off" };
  if (plugin.problem) return { tone: "danger", label: "cannot be switched on" };
  if (plugin.restart_needed) return { tone: "warn", label: plugin.enabled ? "on after restart" : "off after restart" };
  return plugin.loaded ? { tone: "ok", label: "on" } : { tone: "off", label: "off" };
}

function registers(plugin: Plugin): string {
  const parts = [
    plugin.tools.length > 0 && `tools ${plugin.tools.join(", ")}`,
    plugin.providers.length > 0 && `providers ${plugin.providers.join(", ")}`,
    plugin.credential_types.length > 0 && `credential types ${plugin.credential_types.join(", ")}`,
  ].filter(Boolean);
  return parts.length > 0 ? parts.join("; ") : "nothing";
}

/** Global only: the user's own plugins in data/plugins, switched on one by one, and their pages. */
export default function PluginsSection({ level }: { level: SettingsLevel }) {
  const [plugins, setPlugins] = useState<Plugin[] | null>(null);
  const [message, setMessage] = useState<{ error: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [page, setPage] = useState<Plugin | null>(null);
  const [restarting, setRestarting] = useState(false);

  const load = useCallback(async () => {
    const { data, error } = await api.GET("/api/v1/plugins");
    if (error) setMessage({ error: true, text: errorMessage(error, "The plugins could not be read.") });
    setPlugins(data ?? []);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function toggle(plugin: Plugin) {
    const enabled = !plugin.enabled;
    if (enabled && !window.confirm(`Switch on ${plugin.name}?\n\n${RIGHTS_WARNING}\n\nIt is loaded at the next start of YAAP.`)) {
      return;
    }
    setBusy(plugin.name);
    setMessage(null);
    const { error } = await api.PUT("/api/v1/plugins/{name}", {
      params: { path: { name: plugin.name } },
      body: { enabled },
    });
    setBusy(null);
    if (error) setMessage({ error: true, text: errorMessage(error) });
    void load();
  }

  async function restart() {
    if (!window.confirm("Restart YAAP now? Replies in progress stop; the page reloads when YAAP is back.")) return;
    setRestarting(true);
    const { error } = await api.POST("/api/v1/plugins/restart");
    if (error) {
      setRestarting(false);
      setMessage({ error: true, text: errorMessage(error, "YAAP could not restart.") });
      return;
    }
    // Wait until YAAP answers again, then show the new state.
    const started = Date.now();
    const poll = async () => {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      const response = await fetch("/api/v1/plugins").catch(() => null);
      if (response?.ok && Date.now() - started > 3000) {
        window.location.reload();
        return;
      }
      if (Date.now() - started < 120_000) void poll();
      else {
        setRestarting(false);
        setMessage({ error: true, text: "YAAP did not come back within two minutes. Check the container." });
      }
    };
    void poll();
  }

  const restartNeeded = plugins?.some((plugin) => plugin.restart_needed) ?? false;

  return (
    <>
      <SectionHeader
        level={level}
        title="Plugins"
        actions={
          restartNeeded && (
            <button type="button" className="button button-primary" disabled={restarting} onClick={() => void restart()}>
              {restarting ? "Restarting…" : "Restart YAAP now"}
            </button>
          )
        }
        info={
          <p>
            Your own Python packages in <span className="mono">data/plugins/&lt;name&gt;</span>, each with a{" "}
            <span className="mono">plugin.json</span>. A switched-on plugin is loaded when YAAP starts and adds tools,
            model providers and kinds of keys, and can bring a page of plain HTML. That page may call only the API routes
            its <span className="mono">plugin.json</span> lists. A plugin that fails to load is switched off and its
            error is shown here; YAAP starts without it. There is no plugin hub and no install from a link: see
            docs/plugins.md.
          </p>
        }
      />
      <div className="wb-body wb-body-narrow wb-stack">
        <p className="settings-notice">{RIGHTS_WARNING}</p>
        {restartNeeded && (
          <p className="settings-help">Switching a plugin on or off takes effect after a restart of YAAP.</p>
        )}
        {message && <p className={message.error ? "form-error" : "form-ok"}>{message.text}</p>}
        {page?.page_url && (
          <Panel
            title={`${page.name} page`}
            aside={
              <button type="button" className="link-button small" onClick={() => setPage(null)}>
                Close
              </button>
            }
            flush
          >
            <div className="page-preview">
              <PluginFrame name={page.name} url={page.page_url} routes={page.api_routes} />
            </div>
          </Panel>
        )}
        {plugins === null ? (
          <EmptyState>Loading…</EmptyState>
        ) : plugins.length === 0 ? (
          <EmptyState>
            No plugins in <span className="mono">data/plugins</span>. docs/plugins.md shows how to write one.
          </EmptyState>
        ) : (
          plugins.map((plugin) => {
            const state = status(plugin);
            return (
              <Panel
                key={plugin.name}
                title={
                  <>
                    <span className="mono">{plugin.name}</span> {plugin.version && <span className="muted small">{plugin.version}</span>}
                  </>
                }
                aside={<Badge tone={state.tone}>{state.label}</Badge>}
              >
                <div className="wb-stack settings-plugin">
                  {plugin.description && <p>{plugin.description}</p>}
                  {!plugin.problem && <p className="muted small">Registers {registers(plugin)}.</p>}
                  {!plugin.problem && plugin.has_page && (
                    <p className="muted small">
                      {plugin.api_routes.length > 0 ? (
                        <>
                          Its page may call:{" "}
                          {plugin.api_routes.map((route, index) => (
                            <span key={route}>
                              {index > 0 && ", "}
                              <span className="mono">{route}</span>
                            </span>
                          ))}
                          .
                        </>
                      ) : (
                        "Its page calls none of YAAP's API."
                      )}
                    </p>
                  )}
                  {plugin.problem && <p className="form-error">{plugin.problem}</p>}
                  {plugin.error && (
                    <p className="form-error">
                      Switched off because it failed to load
                      {plugin.failed_at ? ` (${new Date(plugin.failed_at).toLocaleString()})` : ""}:{" "}
                      <span className="mono">{plugin.error}</span>
                    </p>
                  )}
                  <div className="settings-plugin-actions">
                    <button
                      type="button"
                      className={plugin.enabled ? "button" : "button button-primary"}
                      disabled={busy === plugin.name || (!plugin.enabled && plugin.problem !== null)}
                      onClick={() => void toggle(plugin)}
                    >
                      {plugin.enabled ? "Switch off" : plugin.error ? "Switch on again…" : "Switch on…"}
                    </button>
                    {plugin.page_url && (
                      <button type="button" className="button" onClick={() => setPage(plugin)}>
                        Open page
                      </button>
                    )}
                  </div>
                </div>
              </Panel>
            );
          })
        )}
      </div>
    </>
  );
}
