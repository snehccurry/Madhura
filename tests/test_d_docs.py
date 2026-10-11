"""[D] Docs: README / CHANGELOG / docs links and images resolve, and the README's screenshots are
exactly the set ``tools/screenshots.py --readme`` renders."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCREENSHOTS = REPO_ROOT / "docs" / "screenshots"


def _load_tool(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"madhura_tool_{name}", REPO_ROOT / "tools" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def check_docs() -> ModuleType:
    return _load_tool("check_docs")


def test_docs_exist(check_docs: ModuleType) -> None:
    names = {p.relative_to(REPO_ROOT).as_posix() for p in check_docs.default_files()}
    assert {"README.md", "CHANGELOG.md", "docs/ARCHITECTURE.md"} <= names


def test_doc_links_images_anchors_and_tables(check_docs: ModuleType) -> None:
    problems = check_docs.check_files(check_docs.default_files())
    assert problems == []


def test_checker_reports_broken_links_anchors_and_tables(check_docs: ModuleType, tmp_path: Path) -> None:
    (tmp_path / "other.md").write_text("# Other page\n\n## Download & install\n", encoding="utf-8")
    (tmp_path / "pic.png").write_bytes(b"")
    doc = tmp_path / "doc.md"
    doc.write_text(
        "# Title\n\n"
        "[ok](other.md#download--install) ![ok](pic.png) [self](#title) [web](https://example.com/x)\n"
        "[gone](missing.md) ![gone](img/none.png) [bad anchor](other.md#nope) [bad self](#nope)\n"
        '<img src="nowhere.png"> <a href="other.md#other-page">fine</a>\n'
        "`[in code](not-checked.md)`\n\n"
        "```\n[in a fence](not-checked-either.md)\n```\n\n"
        "| a | b |\n| --- | --- |\n| 1 | 2 |\n| 1 | 2 | 3 |\n\n"
        "| x | y |\n| 1 | 2 |\n",
        encoding="utf-8",
    )
    problems = check_docs.check_file(doc)
    text = "\n".join(problems)
    for expected in ("missing.md", "img/none.png", "other.md#nope", "'#nope'", "nowhere.png", "3 cells", "delimiter"):
        assert expected in text, (expected, problems)
    assert "not-checked" not in text
    assert len(problems) == 7, problems


@pytest.mark.parametrize(
    ("heading", "slug"),
    [
        ("Download & install", "download--install"),
        ("Upgrading from Madhura 1.x", "upgrading-from-madhura-1x"),
        ("Settings, themes & accessibility", "settings-themes--accessibility"),
        ("From source (Windows, Linux, macOS)", "from-source-windows-linux-macos"),
        ("[2.0.0] — 2026-10-11", "200--2026-10-11"),
        ("The `--self-test` flag", "the---self-test-flag"),
    ],
)
def test_github_slugs(check_docs: ModuleType, heading: str, slug: str) -> None:
    assert check_docs.github_slug(heading) == slug


def test_readme_screenshots_are_the_readme_set() -> None:
    """``docs/screenshots`` holds exactly the curated set, and the README shows every one of them
    (regenerate with ``python tools/screenshots.py --readme``)."""
    shots = _load_tool("screenshots")
    expected = {file for _shot, _theme, file in shots.README_SHOTS}
    assert len(expected) == len(shots.README_SHOTS), "duplicate README file names"
    assert {shot for shot, _theme, _file in shots.README_SHOTS} <= set(shots.SHOTS)
    assert {p.name for p in SCREENSHOTS.iterdir()} == expected
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    used = set(re.findall(r"docs/screenshots/([\w.-]+\.png)", readme))
    assert used == expected
    total = sum((SCREENSHOTS / name).stat().st_size for name in expected)
    assert total < 6_000_000, f"README images total {total / 1e6:.1f} MB"


def test_screenshots_readme_preset_arguments() -> None:
    shots = _load_tool("screenshots")
    args = shots.parse_args(["--readme"])
    assert args.out == shots.README_OUT == SCREENSHOTS
    assert args.scale == shots.README_SCALE
    args = shots.parse_args([])
    assert args.out == shots.DEFAULT_OUT and args.scale == 1.0 and args.theme == "both"
    assert shots.DEFAULT_OUT.relative_to(REPO_ROOT).parts[0] == "build"  # git-ignored
    with pytest.raises(SystemExit):
        shots.parse_args(["--readme", "--only", "hero"])


def test_pyproject_readme_is_the_readme() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")  # no tomllib on 3.10
    assert re.search(r'^readme\s*=\s*"README\.md"\s*$', pyproject, re.MULTILINE)
    names = [p.name for p in REPO_ROOT.iterdir()]
    assert "README.md" in names
    assert [n for n in names if n.lower() == "readme.md"] == ["README.md"]  # no case-colliding copy
    assert re.search(r"^Changelog\s*=\s*\".*/CHANGELOG\.md\"\s*$", pyproject, re.MULTILINE)
    assert (REPO_ROOT / "CHANGELOG.md").is_file()
