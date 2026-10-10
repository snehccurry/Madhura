"""Enforce spec §1.1: only core/clock.py may read the wall clock."""

from __future__ import annotations

import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "madhura"
ALLOWED = {PACKAGE / "core" / "clock.py"}
FORBIDDEN = re.compile(r"datetime\.now\(|date\.today\(|datetime\.today\(|\btime\.time\(")


def test_package_never_reads_the_wall_clock_outside_clock_py() -> None:
    offenders: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if path in ALLOWED:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if FORBIDDEN.search(code):
                offenders.append(f"{path.relative_to(PACKAGE.parent)}:{lineno}: {line.strip()}")
    assert not offenders, "Use ctx.clock / core.clock instead of the wall clock:\n" + "\n".join(offenders)
