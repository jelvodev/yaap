import { indentWithTab } from "@codemirror/commands";
import { HighlightStyle, LanguageDescription, syntaxHighlighting } from "@codemirror/language";
import { languages } from "@codemirror/language-data";
import { Compartment, EditorSelection, EditorState, type Extension } from "@codemirror/state";
import { EditorView, keymap } from "@codemirror/view";
import { tags } from "@lezer/highlight";
import { basicSetup } from "codemirror";
import { useEffect, useRef } from "react";

import "./code-editor.css";

export interface CodeEditorProps {
  /** The text. A new value from outside (not from typing) replaces the document, keeping the cursor where it can. */
  value: string;
  /** Picks the syntax colours by file name or extension, such as `app.py` or `README.md`. */
  filename?: string;
  readOnly?: boolean;
  /** Called with the whole text after each change, with the line endings the text came with. */
  onChange?: (value: string) => void;
  /** Ctrl+S (Cmd+S on a Mac) inside the editor. */
  onSave?: () => void;
  /** A line to put the cursor on and scroll to when the editor opens. */
  initialLine?: number;
  ariaLabel?: string;
  className?: string;
}

/** Syntax colours from YAAP's own tokens (styles.css), so they follow the light and dark theme. */
const highlight = HighlightStyle.define([
  { tag: [tags.keyword, tags.operatorKeyword, tags.modifier, tags.controlKeyword], color: "var(--code-keyword)" },
  { tag: [tags.string, tags.special(tags.string), tags.regexp], color: "var(--code-string)" },
  { tag: [tags.number, tags.integer, tags.float], color: "var(--code-number)" },
  { tag: [tags.bool, tags.null, tags.atom, tags.literal], color: "var(--code-literal)" },
  { tag: [tags.propertyName, tags.definition(tags.propertyName)], color: "var(--code-property)" },
  { tag: [tags.function(tags.variableName), tags.function(tags.propertyName)], color: "var(--code-function)" },
  { tag: [tags.typeName, tags.className, tags.namespace], color: "var(--code-type)" },
  { tag: [tags.comment, tags.lineComment, tags.blockComment, tags.docComment], color: "var(--code-comment)", fontStyle: "italic" },
  { tag: [tags.tagName], color: "var(--code-tag)" },
  { tag: [tags.attributeName], color: "var(--code-attribute)" },
  { tag: [tags.punctuation, tags.bracket, tags.separator], color: "var(--code-punctuation)" },
  { tag: tags.heading, color: "var(--code-keyword)", fontWeight: "700" },
  { tag: tags.strong, fontWeight: "700" },
  { tag: tags.emphasis, fontStyle: "italic" },
  { tag: tags.link, color: "var(--link)", textDecoration: "underline" },
  { tag: tags.invalid, color: "var(--danger)" },
]);

const theme = EditorView.theme({
  "&": { height: "100%", color: "var(--text)", backgroundColor: "var(--code-bg)", fontSize: "13px" },
  ".cm-scroller": { fontFamily: "var(--mono)", lineHeight: "1.55" },
  ".cm-content": { caretColor: "var(--accent)" },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--accent)" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection": {
    backgroundColor: "var(--accent-soft)",
  },
  ".cm-gutters": { backgroundColor: "var(--code-bg)", color: "var(--faint)", borderRight: "1px solid var(--border)" },
  ".cm-activeLine": { backgroundColor: "var(--hover)" },
  ".cm-activeLineGutter": { backgroundColor: "var(--hover-strong)", color: "var(--text)" },
  ".cm-panels": { backgroundColor: "var(--raised)", color: "var(--text)" },
  ".cm-tooltip": { backgroundColor: "var(--raised)", border: "1px solid var(--border-strong)" },
  ".cm-searchMatch": { backgroundColor: "color-mix(in srgb, var(--warning) 25%, transparent)" },
  ".cm-matchingBracket, &.cm-focused .cm-matchingBracket": { backgroundColor: "var(--raised-strong)", outline: "1px solid var(--border-strong)" },
});

function lineEnding(text: string): string {
  return text.includes("\r\n") ? "\r\n" : "\n";
}

function textOf(state: EditorState, eol: string): string {
  return state.doc.sliceString(0, state.doc.length, eol);
}

/**
 * A CodeMirror 6 editor: syntax colours for the common languages by file name, search,
 * Ctrl+S to save, and a read-only mode. It knows nothing about files or saving: the caller owns the text, so the same
 * editor serves the Files tab and the shared document panel.
 */
export default function CodeEditor({
  value,
  filename,
  readOnly = false,
  onChange,
  onSave,
  initialLine,
  ariaLabel = "Editor",
  className,
}: CodeEditorProps) {
  const host = useRef<HTMLDivElement | null>(null);
  const view = useRef<EditorView | null>(null);
  const eol = useRef(lineEnding(value));
  const callbacks = useRef({ onChange, onSave });
  callbacks.current = { onChange, onSave };
  const language = useRef(new Compartment());
  const editable = useRef(new Compartment());

  // The view lives as long as the component; props reach it through compartments and transactions.
  useEffect(() => {
    if (!host.current) return;
    const extensions: Extension[] = [
      basicSetup,
      keymap.of([
        {
          key: "Mod-s",
          preventDefault: true,
          run: () => {
            callbacks.current.onSave?.();
            return true;
          },
        },
        indentWithTab,
      ]),
      theme,
      syntaxHighlighting(highlight),
      language.current.of([]),
      editable.current.of([EditorState.readOnly.of(readOnly), EditorView.editable.of(!readOnly)]),
      EditorView.contentAttributes.of({ "aria-label": ariaLabel }),
      EditorView.updateListener.of((update) => {
        if (update.docChanged) callbacks.current.onChange?.(textOf(update.state, eol.current));
      }),
    ];
    const state = EditorState.create({ doc: value, extensions });
    const created = new EditorView({ state, parent: host.current });
    view.current = created;
    if (initialLine && initialLine > 1 && initialLine <= state.doc.lines) {
      const line = state.doc.line(initialLine);
      created.dispatch({ selection: EditorSelection.cursor(line.from), effects: EditorView.scrollIntoView(line.from, { y: "center" }) });
    }
    return () => {
      created.destroy();
      view.current = null;
    };
    // Created once; later changes of the props are applied by the effects below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A new value from outside replaces the document.
  useEffect(() => {
    const current = view.current;
    if (!current) return;
    if (textOf(current.state, eol.current) === value) return;
    eol.current = lineEnding(value);
    const length = value.replace(/\r\n?/g, "\n").length;
    const anchor = Math.min(current.state.selection.main.head, length);
    current.dispatch({ changes: { from: 0, to: current.state.doc.length, insert: value }, selection: { anchor } });
  }, [value]);

  useEffect(() => {
    view.current?.dispatch({
      effects: editable.current.reconfigure([EditorState.readOnly.of(readOnly), EditorView.editable.of(!readOnly)]),
    });
  }, [readOnly]);

  // Syntax colours load on demand, one small chunk per language.
  useEffect(() => {
    let current = true;
    const description = filename ? LanguageDescription.matchFilename(languages, filename) : null;
    if (!description) {
      view.current?.dispatch({ effects: language.current.reconfigure([]) });
      return;
    }
    void description.load().then((support) => {
      if (current) view.current?.dispatch({ effects: language.current.reconfigure(support) });
    });
    return () => {
      current = false;
    };
  }, [filename]);

  return <div ref={host} className={`code-editor${className ? ` ${className}` : ""}`} />;
}
