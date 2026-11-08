"""The repository map: within its budget, cached per commit, skipping vendored and
generated folders, made through the run's file access, and switched off by its setting."""

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.evals.baseline import build_project
from app.evals.footprint import estimate_tokens
from app.evals.workspace import remove_folder
from app.git.service import run_git
from app.outline.parsers import ParserOutliner
from app.outline.symbols import Outline, Symbol
from app.repomap.builder import DEFAULT_BUDGET_TOKENS, MapSource, RepoMap, build_repo_map
from app.repomap.listing import head_commit, is_skipped
from app.repomap.ranking import Module, is_test, path_score, public_symbols, ranked, spread
from app.repomap.render import render_map
from app.repomap.store import RepoMapStore
from app.providers.fake_provider import ScriptedResponse
from app.targets.container import CONTAINER
from tests.test_chat import send
from tests.test_delegation import project_conversation

CART = "class Cart:\n    def add(self, item):\n        pass\n"
GIT_CONFIG = ("user.name=Test", "user.email=test@example.com", "commit.gpgsign=false", "core.autocrlf=false")


def make_map(folder: Path, store: RepoMapStore, location: str = "test") -> RepoMap:
    source = MapSource(CONTAINER.files(str(folder)), CONTAINER.executor(str(folder)), ParserOutliner(), location)
    return asyncio.run(build_repo_map(source, store))


def write(folder: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


def commit(folder: Path, message: str = "Change") -> None:
    if not (folder / ".git").exists():
        run_git(["init", "--quiet", "-b", "main"], folder)
    run_git(["add", "-A"], folder, config=GIT_CONFIG)
    run_git(["commit", "--quiet", "-m", message], folder, config=GIT_CONFIG)


@pytest.fixture(scope="module")
def baseline(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    folder = tmp_path_factory.mktemp("repo-map") / "stockroom"
    build_project(folder)
    yield folder
    remove_folder(folder)


def test_the_map_of_the_baseline_project_names_its_modules_within_the_budget(baseline: Path, tmp_path: Path):
    made = make_map(baseline, RepoMapStore(tmp_path / "maps"))
    assert made.text.startswith("# Repository map") and not made.cached
    assert estimate_tokens(len(made.text)) <= DEFAULT_BUDGET_TOKENS
    assert "- stockroom/ (" in made.text and "- web/ (" in made.text
    assert "stockroom/inventory.py:" in made.text and "web/src/cart.ts:" in made.text
    # Source modules rank above their tests, and the eval cases in .blueprint stay out.
    assert made.text.index("stockroom/inventory.py") < made.text.index("tests/test_inventory.py")
    assert ".blueprint" not in made.text


def test_a_large_project_is_cut_to_the_budget_with_a_count_of_the_rest(tmp_path: Path):
    project = tmp_path / "large"
    functions = "\n\n".join(f"def operation_{index}(value):\n    return value\n" for index in range(15))
    write(project, {f"src/package_{index}/module_{index}.py": functions for index in range(80)})
    write(project, {f"src/package_{index}/__init__.py": "" for index in range(80)})
    commit(project)
    made = make_map(project, RepoMapStore(tmp_path / "maps"))
    assert estimate_tokens(len(made.text)) <= DEFAULT_BUDGET_TOKENS
    assert len(made.text) > DEFAULT_BUDGET_TOKENS * 3
    assert "more source files not shown" in made.text


def test_the_map_changes_only_when_the_commit_changes(tmp_path: Path):
    project, store = tmp_path / "shop", RepoMapStore(tmp_path / "maps")
    write(project, {"shop/cart.py": CART})
    commit(project)
    first = make_map(project, store)
    again = make_map(project, store)
    assert again.cached and again.text == first.text and "class Cart(add)" in first.text

    # Changes without a commit: the map of the changed tree is made once and then kept, so the prompt cache holds.
    write(project, {"shop/cart.py": CART + "\n\ndef checkout():\n    pass\n"})
    dirty = make_map(project, store)
    assert not dirty.cached and "uncommitted changes" in dirty.text and "checkout()" in dirty.text
    write(project, {"shop/pay.py": "def refund():\n    pass\n"})
    still = make_map(project, store)
    assert still.cached and still.text == dirty.text and "refund" not in still.text

    commit(project, "Pay")
    committed = make_map(project, store)
    assert not committed.cached and "refund()" in committed.text and committed.text != first.text


def test_a_clean_commit_shares_its_map_between_folders(tmp_path: Path):
    store = RepoMapStore(tmp_path / "maps")
    project = tmp_path / "one"
    write(project, {"app/main.py": "def run():\n    pass\n"})
    commit(project)
    clone = tmp_path / "two"
    run_git(["clone", "--quiet", str(project), str(clone)], tmp_path)
    assert not make_map(project, store, "container::one").cached
    assert make_map(clone, store, "container::two").cached


def test_skipped_and_ignored_folders_are_absent(tmp_path: Path):
    project = tmp_path / "web"
    write(
        project,
        {
            "src/app.ts": "export function start(): void {}\n",
            "node_modules/left-pad/index.js": "function leftPad() {}\n",
            ".venv/lib/site.py": "def site_function():\n    pass\n",
            "dist/bundle.js": "function bundled() {}\n",
            "build/out.py": "def built():\n    pass\n",
            "vendor/lib/vendored.py": "def vendored():\n    pass\n",
            ".blueprint/evals/case.md": "# A case\n",
            ".evals/run/case/copy.py": "def copied():\n    pass\n",
            "generated/schema.py": "def generated_schema():\n    pass\n",
            ".gitignore": "generated/\n",
        },
    )
    commit(project)
    text = make_map(project, RepoMapStore(tmp_path / "maps")).text
    assert "src/app.ts: start()" in text
    for absent in ("node_modules", ".venv", "dist", "build/", "vendor", ".blueprint", ".evals", "generated"):
        assert absent not in text


def test_a_folder_outside_git_is_keyed_on_its_file_list(tmp_path: Path):
    project, store = tmp_path / "notes", RepoMapStore(tmp_path / "maps")
    write(project, {"tool/run.py": "def go():\n    pass\n", "node_modules/x/index.js": "function x() {}\n"})
    first = make_map(project, store)
    assert "not a git repository" in first.text and "tool/run.py: go()" in first.text
    assert "node_modules" not in first.text
    write(project, {"tool/run.py": "def go():\n    pass\n\n\ndef stop():\n    pass\n"})
    assert make_map(project, store).cached
    write(project, {"tool/more.py": "def more():\n    pass\n"})
    changed = make_map(project, store)
    assert not changed.cached and "more()" in changed.text


def test_a_folder_inside_another_repository_is_mapped_by_its_own_files(tmp_path: Path):
    outer = tmp_path / "outer"
    write(outer, {"top.py": "def top():\n    pass\n", "inner/part.py": "def part():\n    pass\n"})
    commit(outer)
    text = make_map(outer / "inner", RepoMapStore(tmp_path / "maps")).text
    assert "not a git repository" in text and "part.py: part()" in text and "top" not in text


def test_an_empty_folder_has_no_map(tmp_path: Path):
    (tmp_path / "empty").mkdir()
    assert make_map(tmp_path / "empty", RepoMapStore(tmp_path / "maps")).text == ""


def test_the_store_keeps_the_most_recently_used_maps(tmp_path: Path):
    store = RepoMapStore(tmp_path / "maps", max_entries=2)
    store.put("a", "map a")
    store.put("b", "map b")
    store.put("c", "map c")
    assert len(list((tmp_path / "maps").glob("*.md"))) == 2
    assert store.get("c") == "map c"


@pytest.mark.parametrize(
    ("path", "skipped"),
    [
        ("src/app.py", False),
        ("README.md", False),
        ("node_modules/react/index.js", True),
        ("web/node_modules/react/index.js", True),
        (".github/workflows/ci.yml", True),
        ("backend/.venv/lib/x.py", True),
        ("dist/index.js", True),
        ("pkg/thing.egg-info/PKG-INFO", True),
        ("docs/build.md", False),
    ],
)
def test_which_folders_are_skipped(path: str, skipped: bool):
    assert is_skipped(path) is skipped


COMMIT = "7f4f00da84a50120ea31085b36618c1aa19e1d55"


@pytest.mark.parametrize(
    ("output", "commit"),
    [
        (f"\n{COMMIT}\n", COMMIT),
        (f"\r\n{COMMIT}\r\n", COMMIT),
        (f"backend/\n{COMMIT}\n", None),
        ("", None),
        (None, None),
        ("\nnot-a-hash\n", None),
        ("fatal: not a git repository\n", None),
    ],
)
def test_the_commit_is_read_only_at_the_top_of_its_own_repository(output: str | None, commit: str | None):
    assert head_commit(output) == commit


@pytest.mark.parametrize(
    ("path", "test"),
    [
        ("tests/test_cart.py", True),
        ("src/cart.test.ts", True),
        ("src/__tests__/cart.ts", True),
        ("pkg/cart_test.go", True),
        ("conftest.py", True),
        ("src/cart.py", False),
        ("src/testing_helpers.py", False),
    ],
)
def test_which_files_count_as_tests(path: str, test: bool):
    assert is_test(path) is test


def test_entry_points_and_source_folders_rank_above_tests_and_docs():
    packages = frozenset({"shop"})
    paths = ("shop/cli.py", "shop/util.py", "docs/conf.py", "tests/test_a.py")
    scores = {path: path_score(path, packages) for path in paths}
    assert scores["shop/cli.py"] > scores["shop/util.py"] > scores["docs/conf.py"] > scores["tests/test_a.py"]


def test_a_large_folder_does_not_crowd_out_the_others():
    paths = [f"api/route_{index}.py" for index in range(5)] + ["web/app.ts", "jobs/worker.py"]
    order = spread(paths, lambda path: 2.0 if path.startswith("api/") else 1.0)
    assert set(order[:3]) == {"api/route_0.py", "web/app.ts", "jobs/worker.py"}
    modules = [Module(f"api/route_{index}.py", (), 3.0) for index in range(3)] + [Module("web/app.ts", (), 2.5)]
    assert [module.path for module in ranked(modules)][:2] == ["api/route_0.py", "web/app.ts"]


def test_only_public_symbols_are_shown():
    method = Symbol("method", "add", 2, 3)
    private_method = Symbol("method", "_check", 4, 5)
    outline = Outline(
        (
            Symbol("class", "Cart", 1, 5, children=(method, private_method)),
            Symbol("function", "_helper", 6, 7),
            Symbol("heading", "Notes", 8, 8),
        )
    )
    shown = public_symbols(outline)
    assert [symbol.name for symbol in shown] == ["Cart"] and [child.name for child in shown[0].children] == ["add"]


def test_a_map_without_modules_still_lists_the_folders():
    text = render_map("commit abc1234", ("docs/a.md", "docs/b.md", "README.md"), [], 1_500, 6_000)
    assert "- docs/ (2 files)" in text and "Files at the root: README.md" in text and "## Modules" not in text


def test_a_module_line_shows_classes_with_methods_and_functions():
    module = Module("shop/cart.py", (Symbol("class", "Cart", 1, 9, children=(Symbol("method", "add", 2, 3),)),), 3.0)
    text = render_map("commit abc1234", ("shop/cart.py",), [module], 1_500, 6_000)
    assert "- shop/cart.py: class Cart(add)" in text


def test_a_project_conversation_starts_with_the_map_as_a_cacheable_part(client, fake):
    project, conversation_id = project_conversation(client)
    folder = client.app.state.workspace_dir / project["folder"]
    write(folder, {"shop/cart.py": "class Cart:\n    def add(self, item):\n        pass\n"})
    fake.responses.append(ScriptedResponse(text="Hi."))
    send(client, conversation_id, "Hello")
    system = fake.requests[0].system
    part = next(part for part in system if part.source == "repo_map")
    assert part.cacheable and "shop/cart.py: class Cart(add)" in part.text
    # Right after the other cacheable parts, before the parts that change per reply.
    index = system.index(part)
    assert all(not later.cacheable for later in system[index + 1 :])
    assert any(earlier.cacheable for earlier in system[:index])


def test_the_setting_turns_the_map_off(client, fake):
    project, conversation_id = project_conversation(client)
    folder = client.app.state.workspace_dir / project["folder"]
    write(folder, {"shop/cart.py": "def total():\n    pass\n"})
    response = client.put(f"/api/v1/projects/{project['id']}/settings/repo_map", json={"value": "off"})
    assert response.status_code == 200
    fake.responses.append(ScriptedResponse(text="Hi."))
    send(client, conversation_id, "Hello")
    assert all(part.source != "repo_map" for part in fake.requests[0].system)
