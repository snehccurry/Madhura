# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: the one-folder desktop build of Madhura (Windows first; Linux works too).

    pip install -r requirements-dev.txt          # PyInstaller >= 6.10
    pyinstaller --noconfirm madhura.spec
    dist/Madhura/Madhura --self-test             # headless completeness check: exit 0 = OK

Output: ``dist/Madhura/`` — the ``Madhura`` executable (windowed, no console; no UPX, which
trips antivirus heuristics) and ``_internal/`` (Python, Qt and ``madhura/assets``; at run time
``sys._MEIPASS`` points there, which is what ``madhura.paths.asset()`` resolves against).

* Every ``madhura`` module is bundled (``collect_submodules``): pages and feature services are
  imported lazily, so they must never depend on what the import analysis happens to reach.
* Qt pieces Madhura never uses are left out: Qt's translations (the UI is English-only and no
  QTranslator is installed), the software OpenGL fallback ``opengl32sw.dll`` (widgets are
  raster-painted) and the PDF image-format plugin with the Qt PDF library it drags in.
* On Windows the executable gets the app icon and a version resource (FileDescription /
  ProductName "Madhura", version from ``madhura.__version__``, copyright from LICENSE).
"""

import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve()  # noqa: F821 - SPECPATH is injected by PyInstaller
NAME = "Madhura"
#: Windows version resource "CompanyName"; left out of the resource while blank.
COMPANY = ""


def _version() -> str:
    """``madhura.__version__``, read without importing the package."""
    text = (ROOT / "madhura" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if not match:
        raise SystemExit("madhura/__init__.py has no __version__")
    return match.group(1)


def _copyright() -> str:
    """The "Copyright ..." line of LICENSE (empty when there is none)."""
    try:
        lines = (ROOT / "LICENSE").read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    return next((line.strip() for line in lines if line.strip().lower().startswith("copyright")), "")


def _version_tuple(version: str) -> tuple:
    """"2.0.0" → (2, 0, 0, 0); "2.1.0rc1" → (2, 1, 0, 0)."""
    numbers = []
    for part in version.split(".")[:4]:
        match = re.match(r"\d+", part)
        numbers.append(min(int(match.group()), 0xFFFF) if match else 0)
    return tuple(numbers + [0] * (4 - len(numbers)))


def _version_resource(version: str):
    """The Windows VERSIONINFO resource, built here so it always matches ``__version__``."""
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    numbers = _version_tuple(version)
    strings = {
        "CompanyName": COMPANY,
        "FileDescription": NAME,
        "FileVersion": version,
        "InternalName": NAME,
        "LegalCopyright": _copyright(),
        "OriginalFilename": f"{NAME}.exe",
        "ProductName": NAME,
        "ProductVersion": version,
    }
    return VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=numbers,
            prodvers=numbers,
            mask=0x3F,
            flags=0x0,
            OS=0x40004,  # VOS_NT_WINDOWS32
            fileType=0x1,  # VFT_APP
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo(
                [StringTable("040904B0", [StringStruct(key, value) for key, value in strings.items() if value])]
            ),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),  # U.S. English, Unicode
        ],
    )


def _unused_qt(dest: str) -> bool:
    """True for bundled Qt files Madhura never loads (see the module docstring)."""
    path = dest.replace("\\", "/").lower()
    name = path.rsplit("/", 1)[-1]
    if re.search(r"(^|/)pyside6/(qt/)?translations/", path):  # PySide6/translations on Windows
        return True
    if name.startswith("opengl32sw"):
        return True
    if "/imageformats/" in path and "qpdf" in name:
        return True
    return bool(re.match(r"(lib)?qt6pdf[.d]", name))


VERSION = _version()
#: Executable icon (PyInstaller embeds one on Windows and macOS only).
ICON = {"win32": "icon.ico", "darwin": "icon.icns"}.get(sys.platform)
ICON_PATH = ROOT / "madhura" / "assets" / ICON if ICON else None

a = Analysis(  # noqa: F821 - PyInstaller spec globals
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ROOT / "madhura" / "assets"), "madhura/assets")],
    hiddenimports=collect_submodules("madhura"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "_tkinter"],
    noarchive=False,
    optimize=0,
)
a.binaries = [entry for entry in a.binaries if not _unused_qt(entry[0])]
a.datas = [entry for entry in a.datas if not _unused_qt(entry[0])]

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(ICON_PATH)] if ICON_PATH is not None and ICON_PATH.is_file() else None,
    version=_version_resource(VERSION) if sys.platform == "win32" else None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=NAME,
)
