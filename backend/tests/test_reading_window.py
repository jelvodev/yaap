"""The reading window of read_file and its outline mode."""

import asyncio

import pytest

from app.agent.runs import RunRegistry
from app.outline.parsers import ParserOutliner
from app.outline.symbols import render_outline
from tests.test_agent_tools import call, project, start_run  # noqa: F401

PYTHON_FIXTURE = '''"""A fixture module."""

import functools


class Base(Protocol, metaclass=Meta):
    """Base."""

    def run(self) -> None:
        pass

    class Inner:
        def deep(self, x: int = 1) -> int:
            def local():
                return x

            return local()


@functools.cache
def helper(a, *args, key=None):
    return a


async def fetch(url: str) -> bytes:
    return b""


if TYPE_CHECKING:
    def conditional():
        pass
'''

TYPESCRIPT_FIXTURE = """// Braces in strings and comments must not count. {
import { thing } from "./thing";

/* a block comment with a fake function inside:
export function fake() {}
*/
export interface Options {
  name: string;
  run(): void;
}

export type Mode = "a" | "b"
  | "c";

const pattern = /[{]/g;
const label = `count: ${"}".length} {`;

export function plain(a: number, b = "}"): number {
  return a + b.length;
}

export const arrow = async (value: string): Promise<void> => {
  console.log(value, "{");
};

const notAFunction = (1 + 2) * 3;

export enum Color {
  Red,
  Green,
}

export default class Store<T> extends Base {
  private items: T[] = [];

  constructor(private readonly size: number) {
    super();
  }

  get count(): number {
    return this.items.length;
  }

  async add(item: T): Promise<void> {
    if (this.items.length > this.size) {
      throw new Error("full }");
    }
    this.items.push(item);
  }

  handle = (event: Event) => {
    this.add(event as unknown as T);
  };
}

function* numbers() {
  yield 1;
}
"""


def outline_of(path: str, text: str) -> str:
    return render_outline(path, len(text.splitlines()), asyncio.run(ParserOutliner().outline(path, text)))


def rows(rendered: str) -> list[str]:
    return [row.strip() for row in rendered.splitlines()[1:]]


def write_lines(project, name: str, count: int) -> None:  # noqa: F811
    (project / name).write_text("".join(f"line {number}\n" for number in range(1, count + 1)), encoding="utf-8")


def test_a_default_read_shows_the_first_400_lines_and_says_what_was_left_out(project):  # noqa: F811
    write_lines(project, "long.txt", 1000)
    registry = RunRegistry()
    output, error = call(registry, start_run(registry, project), "read_file", {"path": "long.txt"})
    assert not error
    body, note = output.split("\n\n[")
    assert body.splitlines()[0] == "     1\tline 1" and body.splitlines()[-1] == "   400\tline 400"
    assert len(body.splitlines()) == 400
    assert note.startswith("Lines 1–400 of 1000 shown; lines 401–1000 left out. Continue with offset=401.")
    assert "outline=true" in note


def test_a_file_within_the_window_is_shown_whole_without_a_note(project):  # noqa: F811
    write_lines(project, "short.txt", 400)
    registry = RunRegistry()
    output, _ = call(registry, start_run(registry, project), "read_file", {"path": "short.txt"})
    assert output.splitlines()[-1] == "   400\tline 400" and "left out" not in output


@pytest.mark.parametrize(
    ("arguments", "first", "last", "note"),
    [
        (
            {"offset": 401, "limit": 500},
            401,
            900,
            "Lines 401–900 of 1000 shown; lines 1–400 and 901–1000 left out. Continue with offset=901.",
        ),
        ({"offset": 950}, 950, 1000, "Lines 950–1000 of 1000 shown; lines 1–949 left out. Read any part"),
        ({"limit": 5000}, 1, 1000, None),
        ({"offset": 1, "limit": 2500}, 1, 1000, None),
    ],
)
def test_a_range_past_the_window_reads_what_was_asked(project, arguments, first, last, note):  # noqa: F811
    write_lines(project, "long.txt", 1000)
    registry = RunRegistry()
    output, error = call(registry, start_run(registry, project), "read_file", {"path": "long.txt", **arguments})
    assert not error
    numbered = [row for row in output.splitlines() if "\t" in row]
    assert numbered[0] == f"{first:>6}\tline {first}" and numbered[-1] == f"{last:>6}\tline {last}"
    assert (note is None and "left out" not in output) or (note is not None and f"[{note}" in output)


def test_at_most_2000_lines_are_read_at_once(project):  # noqa: F811
    (project / "huge.txt").write_text("x\n" * 3000, encoding="utf-8")  # short lines, so the line count is the limit
    registry = RunRegistry()
    output, _ = call(registry, start_run(registry, project), "read_file", {"path": "huge.txt", "limit": 2500})
    assert "  2000\tx" in output and "2001\t" not in output
    assert "lines 2001–3000 left out. Continue with offset=2001." in output


def test_long_lines_stop_the_window_early_and_the_note_says_where(project):  # noqa: F811
    (project / "wide.txt").write_text("".join(f"{number} {'x' * 1500}\n" for number in range(1, 101)), encoding="utf-8")
    registry = RunRegistry()
    output, _ = call(registry, start_run(registry, project), "read_file", {"path": "wide.txt"})
    assert len(output) <= 30_000
    shown = [row for row in output.splitlines() if "\t" in row]
    assert f"Lines 1–{len(shown)} of 100 shown; lines {len(shown) + 1}–100 left out." in output


def test_the_outline_of_a_python_file_lists_classes_functions_and_methods_with_their_lines():
    assert rows(outline_of("pkg/fixture.py", PYTHON_FIXTURE)) == [
        "6-17  class Base(Protocol, metaclass=Meta)",
        "9-10    method run(self) -> None",
        "12-17    class Inner",
        "13-17      method deep(self, x: int=1) -> int",
        "20-22  function helper(a, *args, key=None)",
        "25-26  function fetch(url: str) -> bytes",
        "30-31  function conditional()",
    ]


def test_the_outline_of_a_typescript_file_ignores_braces_in_strings_comments_and_regexes():
    assert rows(outline_of("src/store.ts", TYPESCRIPT_FIXTURE)) == [
        "7-10  interface Options",
        "12-13  type Mode",
        '18-20  function plain(a: number, b = "}")',
        "22-24  function arrow",
        "28-31  enum Color",
        "33-54  class Store",
        "36-38    method constructor(private readonly size: number)",
        "40-42    method get count()",
        "44-49    method add(item: T)",
        "51-53    method handle",
        "56-58  function numbers()",
    ]


def test_the_outline_header_and_the_fallbacks():
    rendered = outline_of("src/store.ts", TYPESCRIPT_FIXTURE)
    assert rendered.splitlines()[0].startswith("Outline of src/store.ts (58 lines): line range, then the symbol.")
    markdown = "# Title\n\nText.\n\n## Part one\n\n```\n# not a heading\n```\n\n## Part two\n\nEnd.\n"
    assert rows(outline_of("notes.md", markdown)) == ["1-13  # Title", "5-10    ## Part one", "11-13    ## Part two"]
    assert outline_of("data.csv", "a,b\n1,2\n").startswith("No outline is available for this type of file")
    broken = outline_of("broken.py", "def broken(:\n    pass\n")
    assert broken.startswith("No outline of broken.py: Python could not parse it (line 1:")


def test_read_file_in_outline_mode_returns_the_outline(project):  # noqa: F811
    (project / "store.ts").write_text(TYPESCRIPT_FIXTURE, encoding="utf-8")
    registry = RunRegistry()
    output, error = call(registry, start_run(registry, project), "read_file", {"path": "store.ts", "outline": True})
    assert not error and output == outline_of("store.ts", TYPESCRIPT_FIXTURE)
