"""Check Madhura's Markdown docs: relative links and images resolve, anchors exist, tables are whole.

    python tools/check_docs.py                  # README.md, CHANGELOG.md and docs/**/*.md
    python tools/check_docs.py docs/ARCHITECTURE.md

Checks every Markdown link and image (``[text](target)``, ``![alt](target)``, reference
definitions) and every HTML ``<a href>`` / ``<img src>`` outside code: a relative target must exist
(paths are resolved from the file that links them), and a ``#fragment`` must match a heading of the
target Markdown file, slugged the way GitHub does (``## Download & install`` → ``#download--install``).
Every table row must have as many cells as its header, and the delimiter row must be valid.
External links (http, https, mailto) are not fetched. Prints ``file:line: problem`` for each
problem and exits with 1 when there is any.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from urllib.parse import unquote

REPO_ROOT = Path(__file__).resolve().parent.parent
#: The docs checked by default (globs relative to the repository root).
DEFAULT_PATTERNS: tuple[str, ...] = ("README.md", "CHANGELOG.md", "docs/**/*.md")

_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_CODE_SPAN = re.compile(r"(`+)(.+?)\1")
_MD_LINK = re.compile(r"!?\[(?:[^\[\]]|\[[^\]]*\])*\]\(\s*<?([^()\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_HTML_LINK = re.compile(r"<(?:a|img)\b[^>]*?\s(?:href|src)\s*=\s*\"([^\"]*)\"", re.IGNORECASE)
_REF_DEF = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?(\S+?)>?(?:\s|$)")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$")
_TABLE_DELIMITER = re.compile(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
_EXTERNAL = ("http://", "https://", "mailto:")


def github_slug(heading: str) -> str:
    """The anchor GitHub gives a heading: rendered text, lower case, punctuation dropped
    (letters, digits, ``-`` and ``_`` stay), spaces → ``-``."""
    text = re.sub(r"<[^>]+>", "", heading)  # inline HTML
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)  # links → their text
    text = text.replace("`", "").replace("*", "")
    text = re.sub(r"[^\w\- ]", "", text.strip().lower())
    return text.replace(" ", "-")


def _lines_outside_code(text: str) -> Iterator[tuple[int, str]]:
    """(line number, line with inline code removed) for every line outside fenced code blocks."""
    fence: str | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        match = _FENCE.match(line)
        if match:
            marker = match.group(1)
            if fence is None:
                fence = marker[0] * len(marker)
                continue
            if marker.startswith(fence):
                fence = None
                continue
        if fence is None:
            yield number, _CODE_SPAN.sub("", line)


def heading_anchors(text: str) -> set[str]:
    """Every anchor of a Markdown document (repeated headings get ``-1``, ``-2``, … like GitHub)."""
    anchors: set[str] = set()
    seen: dict[str, int] = {}
    for _number, line in _lines_outside_code(text):
        match = _HEADING.match(line)
        if not match:
            continue
        slug = github_slug(match.group(2))
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        anchors.add(slug if count == 0 else f"{slug}-{count}")
    return anchors


def _links(line: str) -> Iterator[str]:
    yield from _MD_LINK.findall(line)
    yield from _HTML_LINK.findall(line)
    ref = _REF_DEF.match(line)
    if ref:
        yield ref.group(1)


def _table_cells(row: str) -> int:
    body = row.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    return len(re.split(r"(?<!\\)\|", body))


def _check_tables(rows: list[tuple[int, str]]) -> Iterator[tuple[int, str]]:
    """Problems in one block of consecutive ``|`` lines."""
    if len(rows) < 2 or not _TABLE_DELIMITER.match(rows[1][1].strip()):
        yield rows[0][0], "table without a valid delimiter row under its header"
        return
    width = _table_cells(rows[0][1])
    for number, row in rows[1:]:
        cells = _table_cells(row)
        if cells != width:
            yield number, f"table row has {cells} cells, the header has {width}"


def check_file(path: Path, *, anchors_cache: dict[Path, set[str]] | None = None) -> list[str]:
    """Problems found in one Markdown file, as ``"file:line: message"`` strings."""
    cache = {} if anchors_cache is None else anchors_cache
    path = path.resolve()
    text = path.read_text(encoding="utf-8")
    try:
        shown = path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        shown = str(path)
    problems: list[str] = []

    def anchors_of(target: Path) -> set[str]:
        if target not in cache:
            cache[target] = heading_anchors(target.read_text(encoding="utf-8"))
        return cache[target]

    table: list[tuple[int, str]] = []
    for number, line in [*_lines_outside_code(text), (0, "")]:
        if line.lstrip().startswith("|"):
            table.append((number, line))
        elif table:
            problems.extend(f"{shown}:{n}: {msg}" for n, msg in _check_tables(table))
            table = []
        for link in _links(line):
            if link.startswith(_EXTERNAL):
                continue
            target_part, _hash, fragment = link.partition("#")
            target = (path.parent / unquote(target_part)).resolve() if target_part else path
            if not target.exists():
                problems.append(f"{shown}:{number}: missing link target {link!r}")
                continue
            if fragment and target.suffix.lower() == ".md" and unquote(fragment).lower() not in anchors_of(target):
                problems.append(f"{shown}:{number}: no heading for anchor {link!r}")
    return problems


def default_files(root: Path = REPO_ROOT) -> list[Path]:
    """The docs checked when no files are given (only the ones that exist)."""
    files: list[Path] = []
    for pattern in DEFAULT_PATTERNS:
        files.extend(sorted(p for p in root.glob(pattern) if p.is_file()))
    return files


def check_files(files: Iterable[Path]) -> list[str]:
    """Problems found in all *files*."""
    cache: dict[Path, set[str]] = {}
    problems: list[str] = []
    for path in files:
        problems.extend(check_file(path, anchors_cache=cache))
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_docs.py", description="Check links, images, anchors and tables in Markdown docs.")
    parser.add_argument("files", nargs="*", type=Path, help="Markdown files (default: README.md, CHANGELOG.md, docs/**/*.md)")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    files = args.files or default_files()
    problems = check_files(files)
    for problem in problems:
        print(problem)
    print(f"{len(files)} file(s) checked, {len(problems)} problem(s)", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
