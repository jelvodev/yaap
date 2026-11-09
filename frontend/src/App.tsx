import { useCallback, useEffect, useState } from "react";
import { Navigate, Route, Routes, useParams } from "react-router-dom";

import CommandPalette from "./components/CommandPalette";
import Header from "./components/Header";
import { EnvironmentProvider, useEnvironment } from "./environment/EnvironmentProvider";
import { WELCOME_PATH, useFirstRunRedirect } from "./firstRun/useFirstRun";
import ChatPage from "./pages/ChatPage";
import BlueprintPage from "./pages/BlueprintPage";
import SearchPage from "./pages/SearchPage";
import SettingsPage from "./pages/SettingsPage";
import WelcomePage from "./pages/WelcomePage";
import { PreferencesProvider } from "./preferences/PreferencesProvider";
import { api } from "./api/client";
import AuthGate from "./auth/AuthGate";

/** The model gets the date and time with each message; until a time zone is set, the browser's. */
function useBrowserTimezone() {
  useEffect(() => {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (!zone) return;
    void api.GET("/api/v1/settings").then(({ data }) => {
      const setting = data?.find((item) => item.key === "timezone");
      if (setting && !setting.value) void api.PUT("/api/v1/settings/{key}", { params: { path: { key: "timezone" } }, body: { value: zone } });
    });
  }, []);
}

/** The old project page is now the Project tab of the chat's project panel. */
function ProjectRedirect() {
  const { projectId } = useParams();
  return <Navigate to={`/chat?project=${Number(projectId)}&panel=project`} replace />;
}

function Pages() {
  const { active, error } = useEnvironment();
  if (error) return <p className="page-message form-error">{error}</p>;
  if (!active) return <p className="page-message muted">Loading…</p>;
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/chat" replace />} />
      <Route path="/chat" element={<ChatPage key={active.id} environment={active} />} />
      <Route path="/chat/:conversationId" element={<ChatPage key={active.id} environment={active} />} />
      <Route path="/blueprint" element={<BlueprintPage />} />
      <Route path="/settings" element={<SettingsPage />} />
      <Route path="/settings/:section" element={<SettingsPage />} />
      <Route path="/settings/:section/:tab" element={<SettingsPage />} />
      <Route path="/search" element={<SearchPage key={active.id} />} />
      <Route path={WELCOME_PATH} element={<WelcomePage />} />
      {/* Older addresses: the Agents, Git and Usage tabs and the project page moved into the Blueprint, Settings and
          the chat's project panel. */}
      <Route path="/agents" element={<Navigate to="/blueprint?kind=agent" replace />} />
      <Route path="/git" element={<Navigate to="/settings/git" replace />} />
      <Route path="/usage" element={<Navigate to="/settings/usage" replace />} />
      <Route path="/projects/:projectId" element={<ProjectRedirect />} />
      <Route path="*" element={<Navigate to="/chat" replace />} />
    </Routes>
  );
}

function Shell() {
  const [paletteOpen, setPaletteOpen] = useState(false);
  const closePalette = useCallback(() => setPaletteOpen(false), []);
  useBrowserTimezone();
  useFirstRunRedirect();

  useEffect(() => {
    function handleShortcut(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      }
    }
    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
  }, []);

  return (
    <div className="app">
      <Header onOpenPalette={() => setPaletteOpen(true)} />
      <main className="app-main">
        <Pages />
      </main>
      {paletteOpen && <CommandPalette onClose={closePalette} />}
    </div>
  );
}

export default function App() {
  // Nothing loads before the browser is signed in.
  return (
    <AuthGate>
    <PreferencesProvider>
    <EnvironmentProvider>
      <Shell />
    </EnvironmentProvider>
    </PreferencesProvider>
    </AuthGate>
  );
}
