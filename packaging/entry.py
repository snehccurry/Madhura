"""PyInstaller entry point for ``madhura.spec`` — runs Madhura from the frozen bundle.

It lives outside the package on purpose: PyInstaller puts the entry script's folder on its
module search path, and ``madhura/`` holds modules named ``platform``, ``log`` and ``paths``
that would then shadow the standard library's during the build's import analysis.
"""

from __future__ import annotations

from madhura.app import main

if __name__ == "__main__":
    raise SystemExit(main())
