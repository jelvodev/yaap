import { useEffect, useState } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";

import { api } from "../api/client";
import type { components } from "../api/schema";
import { signOut } from "../auth/AuthGate";
import { notifyIfHidden } from "../notifications";

import { useEnvironment } from "../environment/EnvironmentProvider";
import { SearchIcon } from "./icons";
import { Logo } from "./Logo";

type WaitingApproval = components["schemas"]["WaitingApprovalOut"];
type VaultEntry = components["schemas"]["VaultEntryOut"];
type BackupRun = components["schemas"]["BackupRunOut"];

// Expiry changes slowly; the vault is checked every ten minutes.
const VAULT_CHECK_MS = 10 * 60 * 1000;

export default function Header({ onOpenPalette }: { onOpenPalette: () => void }) {
  const { environments, active, selectEnvironment } = useEnvironment();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [replying, setReplying] = useState(0);
  const [approvals, setApprovals] = useState<WaitingApproval[]>([]);
  const [expiring, setExpiring] = useState<VaultEntry[]>([]);
  const [failedBackup, setFailedBackup] = useState<BackupRun | null>(null);
  const onChat = pathname.startsWith("/chat");

  // Away from the chat, show that a reply is still being written, and say when it is done.
  useEffect(() => {
    if (!active || onChat) {
      setReplying(0);
      return;
    }
    let current = true;
    let before: number[] | null = null;
    const check = () =>
      void api
        .GET("/api/v1/environments/{environment_id}/replies", { params: { path: { environment_id: active.id } } })
        .then(({ data }) => {
          if (!current) return;
          const running = data ?? [];
          if (before && before.some((id) => !running.includes(id))) notifyIfHidden("YAAP has replied", "A reply is ready.");
          before = running;
          setReplying(running.length);
        });
    check();
    const timer = window.setInterval(check, 4000);
    return () => {
      current = false;
      window.clearInterval(timer);
    };
  }, [active, onChat]);

  // Approvals that wait in any conversation of this environment.
  useEffect(() => {
    if (!active) return;
    let current = true;
    const seen = new Set<string>();
    const check = () =>
      void api
        .GET("/api/v1/environments/{environment_id}/approvals", { params: { path: { environment_id: active.id } } })
        .then(({ data }) => {
          if (!current) return;
          const waiting = data ?? [];
          for (const approval of waiting) {
            if (!seen.has(approval.approval_id)) {
              seen.add(approval.approval_id);
              notifyIfHidden("YAAP waits for your approval", approval.summary, approval.approval_id);
            }
          }
          setApprovals(waiting);
        });
    check();
    const timer = window.setInterval(check, 4000);
    return () => {
      current = false;
      window.clearInterval(timer);
    };
  }, [active]);

  // Keys and secrets that expired or expire within 14 days; an expired one is announced once.
  useEffect(() => {
    let current = true;
    const announced = new Set<number>();
    const check = () =>
      void api.GET("/api/v1/vault/attention").then(({ data }) => {
        if (!current) return;
        const entries = data ?? [];
        for (const entry of entries) {
          if (entry.expiry === "expired" && !announced.has(entry.id)) {
            announced.add(entry.id);
            notifyIfHidden("A vault entry expired", `${entry.name} (${entry.kind_label}, ${entry.level_name}) needs a new value.`);
          }
        }
        setExpiring(entries);
      });
    check();
    const timer = window.setInterval(check, VAULT_CHECK_MS);
    return () => {
      current = false;
      window.clearInterval(timer);
    };
  }, []);

  // A scheduled backup that failed, or missed a destination, shows until a later one succeeds.
  useEffect(() => {
    let current = true;
    const announced = new Set<number>();
    const check = () =>
      void api.GET("/api/v1/backups/status").then(({ data }) => {
        if (!current) return;
        const failed = data?.failed ?? null;
        if (failed && !announced.has(failed.id)) {
          announced.add(failed.id);
          notifyIfHidden("The backup did not succeed", failed.error ?? "See Settings › System › Backup.");
        }
        setFailedBackup(failed);
      });
    check();
    const timer = window.setInterval(check, 60 * 1000);
    return () => {
      current = false;
      window.clearInterval(timer);
    };
  }, []);

  const expired = expiring.filter((entry) => entry.expiry === "expired").length;
  const vaultLink =
    expiring[0]?.environment_id != null
      ? `/settings/connections/vault?environment=${expiring[0].environment_id}`
      : "/settings/connections/vault?level=global";

  function switchEnvironment(id: number) {
    if (id === active?.id) return;
    selectEnvironment(id);
    // Conversations belong to one environment, so an open conversation is closed on switching.
    navigate("/chat");
  }

  return (
    <>
      <header className="header">
        <Link to="/chat" className="brand" aria-label="YAAP, new conversation">
          <Logo compact blink className="brand-logo" />
        </Link>
        <nav className="header-nav" aria-label="Main">
          <NavLink to="/chat" title={replying ? "YAAP is replying" : undefined}>
            Chat
            {replying > 0 && <span className="running-dot" aria-label="YAAP is replying" />}
          </NavLink>
          {approvals.length > 0 && (
            <Link
              to={`/chat/${approvals[0].conversation_id}`}
              className="approval-count"
              title={approvals.map((approval) => approval.summary).join("\n")}
            >
              {approvals.length} waiting
            </Link>
          )}
          {expiring.length > 0 && (
            <Link
              to={vaultLink}
              className={`vault-attention${expired ? "" : " soon"}`}
              title={expiring.map((entry) => `${entry.name}: ${entry.expiry === "expired" ? "expired" : "expires soon"}`).join("\n")}
            >
              {expired ? `${expired} expired` : `${expiring.length} expiring`}
            </Link>
          )}
          {failedBackup && (
            <Link
              to="/settings/system/backup?level=global"
              className="vault-attention"
              title={failedBackup.error ?? "The last scheduled backup failed."}
            >
              {failedBackup.status === "partial" ? "Backup copy failed" : "Backup failed"}
            </Link>
          )}
          <NavLink to="/blueprint">Blueprint</NavLink>
          <NavLink to="/settings">Settings</NavLink>
        </nav>
        <button type="button" className="palette-trigger" onClick={onOpenPalette}>
          <SearchIcon size={15} />
          <span>Search or jump to…</span>
          <kbd>Ctrl K</kbd>
        </button>
        <div className="environment-switch" role="group" aria-label="Environment">
          {environments.map((environment) => (
            <button
              key={environment.id}
              type="button"
              aria-pressed={environment.id === active?.id}
              onClick={() => switchEnvironment(environment.id)}
            >
              <span className="dot" style={{ background: environment.colour }} />
              {environment.name}
            </button>
          ))}
        </div>
        <button type="button" className="button button-ghost header-sign-out" onClick={() => void signOut()}>
          Sign out
        </button>
      </header>
      <div className="accent-line" />
    </>
  );
}
