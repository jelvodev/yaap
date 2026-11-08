"""The text of a reply as it is read aloud.

Only the reply's text is read: thinking, tool calls and their output, steering notes and summaries are left out. In
that text, code blocks, images, links' addresses and bare URLs are left out and the Markdown marks are removed, so
the browser's voice reads sentences rather than symbols.
"""

import re
from typing import Any

FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
HTML_TAG = re.compile(r"</?[A-Za-z][^>\n]*>")
HEADING = re.compile(r"^ {0,3}#{1,6}\s+(.*?)\s*#*\s*$")
QUOTE = re.compile(r"^\s*(>\s?)+")
LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d{1,9}[.)])\s+(?:\[[ xX]\]\s+)?")
RULE = re.compile(r"^\s*(?:[-*_]\s*){3,}$")
TABLE_DIVIDER = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$")
IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
REFERENCE_LINK = re.compile(r"\[([^\]]+)\]\[[^\]]*\]")
AUTOLINK = re.compile(r"<(?:https?://|mailto:)[^>]+>")
# An address ends before trailing punctuation, which still belongs to the sentence.
BARE_URL = re.compile(r"\bhttps?://[^\s<>()]*[^\s<>().,;:!?'\"]")
INLINE_CODE = re.compile(r"(`+)(.+?)\1")
STRONG = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
EMPHASIS = re.compile(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?!\w)|(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)")
STRIKE = re.compile(r"~~(.+?)~~")
FOOTNOTE = re.compile(r"\[\^[^\]]+\]")
SENTENCE_END = (".", "!", "?", ":", ";", "…")


def reply_text(content: list[dict[str, Any]]) -> str:
    """The text blocks of a stored reply, in order; everything else is left out."""
    return "\n\n".join(str(block.get("text") or "") for block in content if block.get("type") == "text")


def speakable_text(markdown: str) -> str:
    """Markdown as plain sentences to read aloud: code blocks, images and addresses out, marks removed."""
    paragraphs: list[str] = []
    current: list[str] = []
    fence: str | None = None

    def end_paragraph() -> None:
        if current:
            paragraphs.append(" ".join(current))
            current.clear()

    text = HTML_COMMENT.sub("", markdown.replace("\r\n", "\n"))
    for raw in text.split("\n"):
        opening = FENCE.match(raw)
        if fence is not None:
            # Inside a code block: skip until a fence of the same kind and at least the same length.
            if opening and opening.group(1)[0] == fence[0] and len(opening.group(1)) >= len(fence):
                if not raw.strip().lstrip(fence[0]):
                    fence = None
            continue
        if opening:
            end_paragraph()
            fence = opening.group(1)
            continue
        line = raw.rstrip()
        if not line.strip() or RULE.match(line) or TABLE_DIVIDER.match(line):
            end_paragraph()
            continue
        heading = HEADING.match(line)
        if heading:
            end_paragraph()
            current.append(close_sentence(inline(heading.group(1))))
            end_paragraph()
            continue
        line = QUOTE.sub("", line)
        is_item = bool(LIST_ITEM.match(line))
        line = LIST_ITEM.sub("", line)
        if line.strip().startswith("|") or line.count("|") >= 2:
            # A table row: its cells, separated as a list.
            cells = [inline(cell) for cell in line.strip().strip("|").split("|")]
            line = ", ".join(cell for cell in cells if cell)
            is_item = True
        else:
            line = inline(line)
        if not line:
            continue
        if is_item:
            # Every list item and table row is read as a sentence of its own.
            end_paragraph()
            current.append(close_sentence(line))
            end_paragraph()
        else:
            current.append(line)
    end_paragraph()
    return "\n\n".join(paragraph for paragraph in paragraphs if paragraph.strip())


def inline(text: str) -> str:
    text = IMAGE.sub("", text)
    text = LINK.sub(r"\1", text)
    text = REFERENCE_LINK.sub(r"\1", text)
    text = AUTOLINK.sub("", text)
    text = BARE_URL.sub("", text)
    text = FOOTNOTE.sub("", text)
    # Short code in a sentence (a file or command name) is read; its backticks are not.
    text = INLINE_CODE.sub(lambda match: match.group(2).strip(), text)
    text = HTML_TAG.sub(" ", text)
    text = STRONG.sub(r"\2", text)
    text = EMPHASIS.sub(lambda match: match.group(1) or match.group(2), text)
    text = STRIKE.sub(r"\1", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = re.sub(r"\(\s*\)", "", text)
    # What was left out may leave a space before punctuation.
    return re.sub(r"\s+([.,;:!?])", r"\1", " ".join(text.split()))


def close_sentence(text: str) -> str:
    """A heading, list item or table row ends with a full stop, so the voice pauses after it."""
    return text if text.endswith(SENTENCE_END) else f"{text}."
