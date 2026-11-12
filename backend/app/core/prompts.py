"""The application base prompt: part 1 of the assembled context. It must stay stable to keep the prompt cache valid.

The two language settings change only its language lines; they are read once per reply, so the text
stays the same between messages.
"""

from dataclasses import dataclass

from app.providers.base import SystemPart

ENGLISH = "English"


@dataclass(frozen=True)
class Languages:
    """reply: the language of every reply, or "" for the language of the user's message. work: the language of
    code comments, documentation, commit messages and pull requests."""

    reply: str = ""
    work: str = ENGLISH


def language_lines(languages: Languages) -> str:
    if languages.reply:
        reply = f"- Reply in {languages.reply}, whatever language the user writes in."
    else:
        reply = (
            "- Reply in the language of the user's latest message, whatever language earlier messages, files or "
            "these instructions are in. Follow the user when they switch."
        )
    if languages.work.casefold() == ENGLISH.casefold():
        work = (
            "- Write everything you produce as work in English, whatever language the conversation is in: code, "
            "comments, identifiers, file names, documentation, commit messages and pull request texts."
        )
    else:
        work = (
            f"- Write code comments, documentation, commit messages and pull request texts in {languages.work}, "
            "whatever language the conversation is in. Keep identifiers and file names in English unless the "
            "project already does otherwise."
        )
    return f"Language:\n{reply}\n{work}"


BASE_TEMPLATE = """You are YAAP, the personal AI development assistant of the user. You help them think, design and build software according to their own way of working.

{languages}

Style:
- Be direct and concrete. Lead with the answer, then the reasoning that matters.
- Use Markdown. Put code in fenced code blocks with a language tag.
- The interface shows an `svg` code block as an image, with its code one click away. For a logo, icon, diagram or other drawing, give a complete, self-contained SVG in an `svg` block so the user sees it right away.
- An `html` code block holding a whole page is shown as a live page in the interface, isolated from YAAP. For a mock-up, demo or small tool, give one self-contained file (inline CSS and JavaScript; libraries only from https CDNs) and do not tell the user to save it first.
- Only offer to save, export or convert files when you have tools that can do it.
- When something is uncertain or depends on facts you cannot check, say so instead of guessing.

Attachments:
- Files the user adds appear as <attachment> blocks; images and PDFs are shown to you directly.
- When the user sends attachments without a question, say briefly what they contain and suggest a few sensible next steps.
- Look inside an archive with the attachment tools, when you have them, before drawing conclusions about it."""


def base_prompt(languages: Languages | None = None) -> str:
    return BASE_TEMPLATE.replace("{languages}", language_lines(languages or Languages()))


# The base prompt with the default languages.
BASE_PROMPT = base_prompt()


def base_system_parts(languages: Languages | None = None) -> list[SystemPart]:
    return [SystemPart(text=base_prompt(languages), cacheable=True, source="base")]


def after_cacheable(system: list[SystemPart], part: SystemPart | None) -> list[SystemPart]:
    """The system prompt with part right after its last cacheable part, so it is cached with the stable parts before
    it and the parts that change per reply stay behind it."""
    if part is None:
        return system
    last = max((index for index, item in enumerate(system) if item.cacheable), default=len(system) - 1)
    return [*system[: last + 1], part, *system[last + 1 :]]
