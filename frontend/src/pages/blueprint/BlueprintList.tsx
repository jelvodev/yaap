import { type FormEvent, useEffect, useRef, useState } from "react";

import type { BlueprintItem } from "../../api/client";
import { PlusIcon } from "../../components/icons";
import { Badge, EmptyState, ListGroup, ListHead, ListItem } from "../../components/workbench/Workbench";
import type { AgentTemplate } from "./AgentPanels";
import { FILTERS, type Filter, type Main } from "./blueprintRoute";
import { StateBadge } from "./ItemDetail";
import { type ItemKind, kindInfo } from "./model";

interface Props {
  kind: ItemKind;
  filter: Filter;
  main: Main;
  path: string | null;
  /** Items of the open level (undefined while loading) and the part of them that is listed after filtering. */
  items: BlueprintItem[] | undefined;
  kindItems: BlueprintItem[];
  listed: BlueprintItem[];
  templates: AgentTemplate[];
  template: AgentTemplate | null;
  currentName: string;
  currentColour: string | undefined;
  search: string;
  onSearch: (value: string) => void;
  newKind: ItemKind | null;
  newName: string;
  newError: string | null;
  onNewName: (value: string) => void;
  onCancelNew: () => void;
  onCreate: (event: FormEvent) => void;
  onStartNew: (kind: ItemKind) => void;
  onChooseMain: (main: "import" | "github" | "package") => void;
  onFilter: (filter: Filter) => void;
  onOpen: (path: string) => void;
  onOpenTemplate: (template: AgentTemplate) => void;
}

/** The list column: search, the New menu, the new-item form, filters, items and agent templates still to add. */
export default function BlueprintList({
  kind,
  filter,
  main,
  path,
  items,
  kindItems,
  listed,
  templates,
  template,
  currentName,
  currentColour,
  search,
  onSearch,
  newKind,
  newName,
  newError,
  onNewName,
  onCancelNew,
  onCreate,
  onStartNew,
  onChooseMain,
  onFilter,
  onOpen,
  onOpenTemplate,
}: Props) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    function close(event: MouseEvent) {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    }
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [menuOpen]);

  const missingTemplates = templates.filter((entry) => !kindItems.some((item) => item.name === entry.name));

  function choose(next: "import" | "github" | "package") {
    setMenuOpen(false);
    onChooseMain(next);
  }

  return (
    <>
      <ListHead>
        <input
          value={search}
          placeholder={`Filter ${kindInfo(kind).title.toLowerCase()}`}
          aria-label={`Filter ${kindInfo(kind).title.toLowerCase()}`}
          onChange={(event) => onSearch(event.target.value)}
        />
        <div className="bp-menu" ref={menuRef}>
          <button type="button" className="button button-primary small" aria-expanded={menuOpen} onClick={() => setMenuOpen((value) => !value)}>
            <PlusIcon size={14} /> New
          </button>
          {menuOpen && (
            <div className="bp-menu-list" role="menu">
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setMenuOpen(false);
                  onStartNew(kind);
                }}
              >
                New {kindInfo(kind).singular} in {currentName}
              </button>
              <button type="button" role="menuitem" onClick={() => choose("import")}>
                Import Markdown or zip…
              </button>
              <button type="button" role="menuitem" onClick={() => choose("github")}>
                Skill from GitHub…
              </button>
              <button type="button" role="menuitem" onClick={() => choose("package")}>
                Add from a package…
              </button>
            </div>
          )}
        </div>
      </ListHead>
      {newKind && newKind !== "instructions" && (
        <form className="wb-list-head bp-new-form" onSubmit={onCreate}>
          <input
            autoFocus
            value={newName}
            aria-label={`Name of the new ${kindInfo(newKind).singular}`}
            placeholder={`${kindInfo(newKind).singular} name, e.g. api-design`}
            onChange={(event) => onNewName(event.target.value)}
          />
          <button type="submit" className="button small">
            Create
          </button>
          <button type="button" className="button button-ghost small" onClick={onCancelNew}>
            Cancel
          </button>
          {newError && <p className="form-error small bp-full">{newError}</p>}
        </form>
      )}
      <div className="wb-list-head bp-filters" role="group" aria-label="Show">
        {FILTERS.map((entry) => (
          <button
            key={entry.value}
            type="button"
            className="bp-filter"
            aria-pressed={filter === entry.value}
            onClick={() => onFilter(entry.value)}
          >
            {entry.label}
          </button>
        ))}
      </div>
      {items === undefined && <EmptyState>Loading…</EmptyState>}
      {items !== undefined && listed.length === 0 && (
        <EmptyState>
          {kindItems.length === 0 ? `No ${kindInfo(kind).title.toLowerCase()} at this level yet.` : "Nothing matches this filter."}
        </EmptyState>
      )}
      {listed.map((item) => (
        <ListItem
          key={item.path}
          active={main === "item" && item.path === path}
          muted={item.disabled || item.orphan_extension}
          onClick={() => onOpen(item.path)}
          title={item.kind === "instructions" ? "General instructions" : item.name}
          badges={<StateBadge item={item} levelName={currentName} colour={currentColour} />}
          subtitle={
            [item.summary, item.rules.length ? `${item.rules.length} rule${item.rules.length === 1 ? "" : "s"}` : ""]
              .filter(Boolean)
              .join(" · ") || <span className="faint">No summary</span>
          }
        />
      ))}
      {kind === "agent" && missingTemplates.length > 0 && (
        <>
          <ListGroup>Start from a template</ListGroup>
          {missingTemplates.map((entry) => (
            <ListItem
              key={entry.name}
              active={main === "template" && template?.name === entry.name}
              onClick={() => onOpenTemplate(entry)}
              title={entry.name}
              badges={<Badge>template</Badge>}
              subtitle={entry.description}
            />
          ))}
        </>
      )}
    </>
  );
}
