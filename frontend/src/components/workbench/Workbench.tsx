import { type CSSProperties, type ReactNode, useState } from "react";

import "./workbench.css";

/**
 * The workbench frame every tab except the chat uses: a narrow rail for level and section,
 * an optional list, and a detail across the rest of the width.
 */
export function Workbench({
  rail,
  list,
  children,
  label,
}: {
  rail: ReactNode;
  list?: ReactNode;
  children: ReactNode;
  label: string;
}) {
  return (
    <div className={list ? "wb wb-with-list" : "wb"}>
      <nav className="wb-rail" aria-label={label}>
        {rail}
      </nav>
      {list && <div className="wb-list">{list}</div>}
      <section className="wb-main">{children}</section>
    </div>
  );
}

export function RailHeading({ children }: { children: ReactNode }) {
  return <h2 className="wb-rail-heading">{children}</h2>;
}

export function RailItem({
  active,
  onClick,
  children,
  count,
  depth = 0,
  title,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
  count?: ReactNode;
  depth?: 0 | 1 | 2;
  title?: string;
}) {
  return (
    <button
      type="button"
      className={`wb-rail-item wb-depth-${depth}`}
      aria-current={active}
      onClick={onClick}
      title={title}
    >
      <span className="wb-rail-label">{children}</span>
      {count !== undefined && <span className="wb-rail-count">{count}</span>}
    </button>
  );
}

/** A segmented switch, for a few mutually exclusive views. */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: { value: T; label: ReactNode }[];
  value: T;
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div className="wb-segmented" role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function ListHead({ children }: { children: ReactNode }) {
  return <div className="wb-list-head">{children}</div>;
}

export function ListGroup({ children }: { children: ReactNode }) {
  return <div className="wb-list-group">{children}</div>;
}

export function ListItem({
  active,
  onClick,
  title,
  badges,
  subtitle,
  muted,
}: {
  active: boolean;
  onClick: () => void;
  title: ReactNode;
  badges?: ReactNode;
  subtitle?: ReactNode;
  muted?: boolean;
}) {
  return (
    <button
      type="button"
      className={muted ? "wb-list-item wb-list-item-muted" : "wb-list-item"}
      aria-current={active}
      onClick={onClick}
    >
      <span className="wb-list-title">
        <span className="wb-list-name">{title}</span>
        {badges}
      </span>
      {subtitle && <span className="wb-list-subtitle">{subtitle}</span>}
    </button>
  );
}

export type BadgeTone = "neutral" | "level" | "over" | "off" | "ok" | "warn" | "extended" | "danger" | "accent";

export function Badge({
  tone = "neutral",
  colour,
  children,
  title,
}: {
  tone?: BadgeTone;
  /** For a level badge: the environment's colour. */
  colour?: string;
  children: ReactNode;
  title?: string;
}) {
  return (
    <span
      className={`wb-badge wb-badge-${tone}`}
      style={colour ? ({ "--badge-colour": colour } as CSSProperties) : undefined}
      title={title}
    >
      {children}
    </span>
  );
}

/** Title, breadcrumb, badges and actions of the detail; the explanation sits behind an (i). */
export function PageHeader({
  crumb,
  title,
  badges,
  actions,
  info,
}: {
  crumb?: ReactNode;
  title: ReactNode;
  badges?: ReactNode;
  actions?: ReactNode;
  info?: ReactNode;
}) {
  const [showInfo, setShowInfo] = useState(false);
  return (
    <header className="wb-page-header">
      <div className="wb-page-heading">
        {crumb && <div className="wb-crumb">{crumb}</div>}
        <div className="wb-title-row">
          <h1>{title}</h1>
          {info && (
            <button
              type="button"
              className="wb-info"
              aria-expanded={showInfo}
              aria-label="About this page"
              onClick={() => setShowInfo((open) => !open)}
            >
              i
            </button>
          )}
          {badges}
        </div>
      </div>
      {actions && <div className="wb-actions">{actions}</div>}
      {info && showInfo && <div className="wb-info-text">{info}</div>}
    </header>
  );
}

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
  label,
}: {
  tabs: { value: T; label: ReactNode }[];
  value: T;
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div className="wb-tabs" role="tablist" aria-label={label}>
      {tabs.map((tab) => (
        <button
          key={tab.value}
          type="button"
          role="tab"
          aria-selected={tab.value === value}
          onClick={() => onChange(tab.value)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

/** A card with a title bar, for the sections of a detail. */
export function Panel({
  title,
  aside,
  children,
  flush,
}: {
  title: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
  /** No padding, for tables and lists that run to the edge. */
  flush?: boolean;
}) {
  return (
    <section className="wb-panel">
      <div className="wb-panel-head">
        <h3>{title}</h3>
        {aside && <div className="wb-panel-aside">{aside}</div>}
      </div>
      <div className={flush ? "wb-panel-body wb-flush" : "wb-panel-body"}>{children}</div>
    </section>
  );
}

/** A label and its control on one row, as in a settings form. */
export function FieldRow({ label, hint, children }: { label: ReactNode; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="wb-field">
      <div className="wb-field-label">
        <b>{label}</b>
        {hint && <span>{hint}</span>}
      </div>
      <div className="wb-field-value">{children}</div>
    </div>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <p className="wb-empty">{children}</p>;
}
