"""[D] Packaging: app icon assets, ``--self-test``, the PyInstaller spec / entry script and CI."""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

import madhura
from madhura import app as appmod
from madhura import paths, selftest

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = REPO_ROOT / "madhura"
ASSETS = PACKAGE / "assets"
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def _subprocess_env() -> dict[str, str]:
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONPATH": str(REPO_ROOT), "PYTHONIOENCODING": "utf-8"}
    env.pop("MADHURA_DATA_DIR", None)  # the self-test makes its own
    return env


def _load_tool(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"madhura_tool_{name}", REPO_ROOT / "tools" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- self-test


def test_self_test_lists_every_module() -> None:
    """``MODULES`` drives the frozen build's import check, so it must name every module."""
    names = set()
    for path in PACKAGE.rglob("*.py"):
        parts = path.relative_to(REPO_ROOT).with_suffix("").parts
        names.add(".".join(parts[:-1] if parts[-1] == "__init__" else parts))
    names.discard("madhura.__main__")
    assert sorted(selftest.MODULES) == sorted(names)
    assert len(set(selftest.MODULES)) == len(selftest.MODULES)


def test_self_test_flag_is_parsed() -> None:
    assert appmod.parse_args([]).self_test is None
    assert appmod.parse_args(["--self-test"]).self_test == ""
    assert appmod.parse_args(["--self-test", "report.txt"]).self_test == "report.txt"
    assert appmod.parse_args(["--self-test", "-platform", "offscreen"]).self_test == ""


def test_main_runs_only_the_self_test(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(selftest, "run", lambda report=None, argv=None: calls.append((report, argv)) or 7)
    monkeypatch.setattr(appmod.win_platform, "set_app_user_model_id", lambda *_a: pytest.fail("startup ran"))
    assert appmod.main(["madhura", "--self-test", "out.txt"]) == 7
    assert calls == [("out.txt", ["madhura", "--self-test", "out.txt"])]
    calls.clear()
    assert appmod.main(["madhura", "--self-test"]) == 7
    assert calls == [(None, ["madhura", "--self-test"])]


def test_self_test_passes(tmp_path: Path) -> None:
    report = tmp_path / "report.txt"
    done = subprocess.run(
        [sys.executable, "-m", "madhura", "--self-test", str(report)],
        cwd=tmp_path,
        env=_subprocess_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        check=False,
    )
    text = report.read_text(encoding="utf-8")
    assert done.returncode == 0, text + done.stderr
    assert text.rstrip().endswith(f"Madhura {madhura.__version__} self-test: OK")
    assert "FAIL" not in text
    for key in ("focus", "habits", "insights", "settings"):
        assert f"ok    {key} page" in text
    assert done.stdout == text  # also printed


def test_self_test_reports_a_missing_module_and_asset(tmp_path: Path) -> None:
    """What a frozen build that lost a page module and the chime would report."""
    code = (
        "import sys\n"
        "sys.modules['madhura.ui.pages.insights_page'] = None\n"
        "from madhura import paths, selftest\n"
        "real = paths.asset\n"
        "paths.asset = lambda rel: real('missing/' + str(rel)) if 'chime' in str(rel) else real(rel)\n"
        "raise SystemExit(selftest.run(sys.argv[1]))\n"
    )
    report = tmp_path / "report.txt"
    done = subprocess.run(
        [sys.executable, "-c", code, str(report)],
        cwd=tmp_path,
        env=_subprocess_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        check=False,
    )
    text = report.read_text(encoding="utf-8")
    assert done.returncode == 1, text + done.stderr
    fails = [line for line in text.splitlines() if line.startswith("FAIL")]
    assert any("madhura.ui.pages.insights_page" in line for line in fails)
    assert any(line.startswith("FAIL  asset sounds/chime.wav") for line in fails)
    assert any(line.startswith("FAIL  insights page") for line in fails)
    assert any(line.startswith("FAIL  no errors logged") for line in fails)
    assert "self-test: FAILED" in text.splitlines()[-1]


# --------------------------------------------------------------------------- app icon


def test_icon_assets_exist_with_every_size(qapp) -> None:
    from PySide6.QtGui import QImageReader

    png = QImageReader(str(ASSETS / "icon.png"))
    assert png.imageCount() == 1 and (png.size().width(), png.size().height()) == (512, 512)
    ico = QImageReader(str(ASSETS / "icon.ico"))
    sizes = []
    for _ in range(ico.imageCount()):
        image = ico.read()
        assert not image.isNull() and image.width() == image.height()
        sizes.append(image.width())
        ico.jumpToNextImage()
    assert sorted(sizes) == ICO_SIZES
    icns = ASSETS / "icon.icns"
    if icns.exists():
        assert icns.read_bytes()[:4] == b"icns"


def test_app_icon_has_native_small_and_large_sizes(qapp) -> None:
    icon = appmod._app_icon()  # noqa: SLF001
    sizes = {s.width() for s in icon.availableSizes()}
    assert {16, 32, 256, 512} <= sizes
    assert appmod.APP_ICON_FILES[0] == ("icon.ico" if sys.platform == "win32" else "icon.png")
    assert all(paths.asset(name).is_file() for name in appmod.APP_ICON_FILES)


def _pixels(image: Any) -> bytes:
    from PySide6.QtGui import QImage

    img = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return bytes(img.constBits())[: img.sizeInBytes()]


def test_committed_icons_match_the_generator(qapp, tmp_path: Path) -> None:
    """``tools/make_assets.py`` must still produce the committed icons (re-run it after changing
    the logo or the indigo accent). Pixels are compared with a small tolerance, not bytes."""
    from PySide6.QtGui import QImageReader

    tool = _load_tool("make_assets")
    tool.write_assets(tmp_path)
    for name in ("icon.png", "icon.ico"):
        fresh, committed = QImageReader(str(tmp_path / name)), QImageReader(str(ASSETS / name))
        assert fresh.imageCount() == committed.imageCount(), name
        for _ in range(committed.imageCount()):
            a, b = _pixels(fresh.read()), _pixels(committed.read())
            assert len(a) == len(b), name
            worst = max(abs(x - y) for x, y in zip(a, b, strict=True))
            assert worst <= 16, f"{name}: differs from tools/make_assets.py output (max channel delta {worst})"
            fresh.jumpToNextImage()
            committed.jumpToNextImage()


def test_logo_geometry_comes_from_the_icon_set(qapp) -> None:
    tool = _load_tool("make_assets")
    ring_r, dx, dy, dot_r = tool.logo_geometry()
    assert ring_r == pytest.approx(8.8)
    assert dx > 0 > dy  # the dot sits up and to the right
    assert 0 < dot_r < 1


# --------------------------------------------------------------------------- PyInstaller


class _Target:
    """Records how madhura.spec calls a PyInstaller build class (Analysis, PYZ, EXE, COLLECT)."""

    calls: dict[str, SimpleNamespace] = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        _Target.calls[type(self).__name__] = SimpleNamespace(args=args, kwargs=kwargs)
        self.binaries: list[tuple[str, str, str]] = [
            ("PySide6/Qt/translations/qtbase_de.qm", "x", "DATA"),
            ("PySide6/Qt/lib/libQt6Gui.so.6", "x", "BINARY"),
        ]
        self.datas = list(kwargs.get("datas", []))
        self.pure: list[Any] = []
        self.scripts: list[Any] = []


@pytest.fixture(scope="module")
def spec() -> SimpleNamespace:
    """madhura.spec executed once with recording stand-ins: ``.ns`` (its globals), ``.calls``."""
    pytest.importorskip("PyInstaller.utils.hooks")
    _Target.calls = {}
    namespace: dict[str, Any] = {"SPECPATH": str(REPO_ROOT), "__name__": "__main__"}
    for name in ("Analysis", "PYZ", "EXE", "COLLECT"):
        namespace[name] = type(name, (_Target,), {})
    path = REPO_ROOT / "madhura.spec"
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)  # noqa: S102
    return SimpleNamespace(ns=namespace, calls=dict(_Target.calls))


def test_pyinstaller_spec(spec: SimpleNamespace) -> None:
    ns, calls = spec.ns, spec.calls
    analysis = calls["Analysis"]
    entry = Path(analysis.args[0][0])
    assert entry.is_file() and entry.parent.name == "packaging"  # outside the package (see entry.py)
    assert analysis.kwargs["datas"] == [(str(ASSETS), "madhura/assets")]
    assert set(selftest.MODULES) <= set(analysis.kwargs["hiddenimports"])
    exe = calls["EXE"].kwargs
    assert exe["name"] == "Madhura" and exe["console"] is False and exe["upx"] is False
    assert exe["exclude_binaries"] is True  # one-folder build
    if sys.platform == "win32":
        assert exe["icon"] == [str(ASSETS / "icon.ico")]
        resource = exe["version"]
        assert resource.toRaw()  # serializes
        text = str(resource)
        for key, value in (("FileDescription", "Madhura"), ("ProductName", "Madhura"), ("ProductVersion", madhura.__version__)):
            assert f"StringStruct('{key}', '{value}')" in text
        assert "CompanyName" not in text  # blank → left out
    else:
        assert exe["version"] is None
    collect = calls["COLLECT"].kwargs
    assert collect["name"] == "Madhura" and collect["upx"] is False
    assert ns["a"].binaries == [("PySide6/Qt/lib/libQt6Gui.so.6", "x", "BINARY")]  # translations dropped
    assert ns["VERSION"] == madhura.__version__


@pytest.mark.parametrize(
    ("dest", "unused"),
    [
        ("PySide6\\opengl32sw.dll", True),
        ("PySide6\\translations\\qtbase_de.qm", True),
        ("PySide6/Qt/translations/qt_fr.qm", True),
        ("PySide6\\plugins\\imageformats\\qpdf.dll", True),
        ("PySide6/Qt/plugins/imageformats/libqpdf.so", True),
        ("PySide6\\Qt6Pdf.dll", True),
        ("PySide6/Qt/lib/libQt6Pdf.so.6", True),
        ("PySide6\\Qt6Gui.dll", False),
        ("PySide6\\Qt6Svg.dll", False),
        ("PySide6\\plugins\\imageformats\\qico.dll", False),
        ("PySide6\\plugins\\platforms\\qoffscreen.dll", False),
        ("madhura/assets/sounds/chime.wav", False),
    ],
)
def test_spec_drops_only_unused_qt_files(spec: SimpleNamespace, dest: str, unused: bool) -> None:
    assert spec.ns["_unused_qt"](dest) is unused


@pytest.mark.parametrize(
    ("dest", "platform", "unused"),
    [
        ("madhura\\assets\\fonts\\Inter-Regular.otf", "win32", True),
        ("madhura/assets/fonts/OFL.txt", "win32", True),
        ("madhura/assets/fonts/Inter-Regular.otf", "linux", False),
        ("madhura/assets/fonts/InterDisplay-Light.otf", "darwin", False),
        ("madhura\\assets\\sounds\\chime.wav", "win32", False),
        ("madhura\\assets\\icon.ico", "win32", False),
    ],
)
def test_spec_leaves_inter_out_of_the_windows_build(spec: SimpleNamespace, dest: str, platform: str, unused: bool) -> None:
    """Windows renders with Segoe UI, so the bundled Inter fonts would only add weight there."""
    assert spec.ns["_unused_asset"](dest, platform) is unused


def test_spec_version_numbers(spec: SimpleNamespace) -> None:
    ns = spec.ns
    assert ns["_version_tuple"]("2.0.0") == (2, 0, 0, 0)
    assert ns["_version_tuple"]("2.1.0rc1") == (2, 1, 0, 0)
    assert ns["_version_tuple"]("3") == (3, 0, 0, 0)
    assert ns["_copyright"]().startswith("Copyright")


def test_entry_script_runs_madhura(tmp_path: Path) -> None:
    done = subprocess.run(
        [sys.executable, str(REPO_ROOT / "packaging" / "entry.py"), "--version"],
        cwd=tmp_path,
        env=_subprocess_env(),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == f"Madhura {madhura.__version__}"


# --------------------------------------------------------------------------- CI


def test_ci_workflow_names_existing_tests() -> None:
    """The Windows job runs an explicit list of test files; a renamed file must not drop out."""
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    listed = re.findall(r"tests/test_\w+\.py", workflow)
    assert listed, "no test files listed"
    missing = [name for name in listed if not (REPO_ROOT / name).is_file()]
    assert not missing, missing
    assert "tests/test_d_packaging.py" in listed
