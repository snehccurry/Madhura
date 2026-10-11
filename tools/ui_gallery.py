"""Madhura UI kit gallery: renders every widget in its states and saves screenshots.

Usage::

    QT_QPA_PLATFORM=offscreen python tools/ui_gallery.py [--out DIR] [--themes dark,light]
                                                         [--sections] [--no-scaled] [--show]

Writes ``gallery_<theme>.png`` (and with ``--sections`` one PNG per card under
``sections/``), then re-runs itself in a subprocess with ``QT_SCALE_FACTOR=1.5`` to produce
``gallery_dark_1.5x.png`` (skip with ``--no-scaled``).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from madhura.ui import fonts, theme  # noqa: E402
from madhura.ui.dialogs import StyledDialog  # noqa: E402
from madhura.ui.widgets import (  # noqa: E402
    Badge,
    Button,
    Caption,
    Card,
    Checkbox,
    Chip,
    ChipGroup,
    ConfirmSheet,
    DayChips,
    EmptyState,
    FloatingPanel,
    Heading,
    IconButton,
    InlineBanner,
    Kbd,
    LinkButton,
    ListRow,
    Menu,
    Micro,
    PageHeader,
    Popover,
    ProgressRing,
    SectionHeader,
    SegmentedControl,
    Select,
    SettingsGroup,
    SheetHost,
    Slider,
    Stepper,
    TextArea,
    TextField,
    TimeField,
    ToastHost,
    Toggle,
)
from madhura.ui.widgets._common import show_focus_ring  # noqa: E402

DEFAULT_OUT = Path(os.environ.get("MADHURA_SHOTS_DIR", ROOT / "shots"))


# --------------------------------------------------------------------------- helpers


def _hover(widget: QWidget) -> QWidget:
    """Simulate the hover state of a kit widget (gallery only — pokes private faders)."""
    fader = getattr(widget, "_hover", None)
    if fader is not None and hasattr(fader, "set_target"):
        fader.set_target(1.0, animate=False)
    if isinstance(widget, ListRow):
        widget._hovered = True  # noqa: SLF001
        widget._sync_actions()  # noqa: SLF001
    return widget


def _row(*widgets: QWidget, spacing: int = 10, stretch: bool = True) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for x in widgets:
        lay.addWidget(x, 0, Qt.AlignmentFlag.AlignVCenter)
    if stretch:
        lay.addStretch(1)
    return w


def _col(*widgets: QWidget, spacing: int = 10) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for x in widgets:
        lay.addWidget(x)
    return w


def _label(text: str) -> QLabel:
    return Micro(text)


def _snapshot(widget: QWidget) -> QLabel:
    """A QLabel showing a grab of ``widget`` (for top-level / overlay previews)."""
    widget.ensurePolished()
    if widget.layout() is not None:
        widget.layout().activate()
    pm = widget.grab()
    lab = QLabel()
    lab.setPixmap(pm)
    lab.setFixedSize(pm.deviceIndependentSize().toSize())
    return lab


class _Backdrop(QWidget):
    """Window-like surface painted with the ``bg`` token."""

    def paintEvent(self, _e: object) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.palette().bg))
        p.end()


# --------------------------------------------------------------------------- sections


def sec_type() -> Card:
    c = Card("Typography", "Inter / Segoe UI Variable · type scale")
    c.body.addWidget(Heading("Plan your next hour", "h1"))
    c.body.addWidget(Heading("Up next", "h2"))
    body = QLabel("Body 14 — Decide what the next block of time is for.")
    body.setProperty("role", "body")
    c.body.addWidget(body)
    lab = QLabel("Label 13/600 — Work deeply on your main project")
    lab.setProperty("role", "label")
    c.body.addWidget(lab)
    c.body.addWidget(Caption("Caption 12 — Ends at 14:35 · check-ins every 7 min"))
    c.body.addWidget(Micro("Micro caps · this week"))
    tones = QWidget()
    tl = QHBoxLayout(tones)
    tl.setContentsMargins(0, 0, 0, 0)
    tl.setSpacing(14)
    for tone in ("muted", "subtle", "accent", "success", "warning", "danger", "break"):
        q = QLabel(tone)
        q.setProperty("tone", tone)
        q.setFont(fonts.font(13, 500))
        tl.addWidget(q)
    tl.addStretch(1)
    c.body.addWidget(tones)
    return c


def sec_buttons() -> Card:
    c = Card("Buttons", "primary · secondary · ghost · danger · danger_solid")
    for size in ("lg", "md", "sm"):
        c.body.addWidget(
            _row(
                Button("Lock in", "primary", icon="play" if size != "sm" else None, size=size),
                Button("Pause", "secondary", size=size),
                Button("Not now", "ghost", size=size),
                Button("Delete", "danger", icon="trash" if size == "md" else None, size=size),
                Button("Reset", "danger_solid", size=size),
                spacing=8,
            )
        )
    hov = [
        _hover(Button("Hover", "primary")),
        _hover(Button("Hover", "secondary")),
        _hover(Button("Hover", "ghost")),
    ]
    disabled = Button("Disabled", "primary")
    disabled.setEnabled(False)
    dis2 = Button("Disabled", "secondary")
    dis2.setEnabled(False)
    c.body.addWidget(_row(*hov, disabled, dis2, spacing=8))
    focus_btn = Button("Keyboard focus", "secondary", icon="keyboard")
    c.body.addWidget(_row(focus_btn, Button("+5 min", "secondary"), Button("Finish", "secondary", icon="check"), spacing=8))
    c._focus_target = focus_btn  # type: ignore[attr-defined]
    return c


def sec_icon_buttons() -> Card:
    c = Card("Icon buttons & links")
    ib = [
        IconButton("pause", "Pause · Space"),
        _hover(IconButton("eye_off", "Hide to mini timer · Ctrl+H")),
        IconButton("more", "More"),
        IconButton("settings", "Settings", variant="secondary"),
        IconButton("play", "Start", variant="primary"),
        IconButton("x", "Close", 28),
        IconButton("plus", "Add", 24),
    ]
    checked = IconButton("bell", "Reminders", checkable=True)
    checked.setChecked(True)
    dis = IconButton("trash", "Delete")
    dis.setEnabled(False)
    c.body.addWidget(_row(*ib, checked, dis, spacing=6))
    c.body.addWidget(_row(LinkButton("Not a focus block?"), _hover(LinkButton("Wrap up the day", tone="accent")), spacing=16))
    c.body.addWidget(_row(LinkButton("Resume nudges", tone="accent", icon="bell"), LinkButton("Discard", tone="danger"), spacing=16))
    return c


def sec_chips() -> Card:
    c = Card("Chips & segmented controls")
    durations = ChipGroup([(15, "15m"), (25, "25m"), (45, "45m"), (60, "1h"), (90, "90m"), ("custom", "Custom")], value=60)
    c.body.addWidget(_label("ChipGroup · exclusive"))
    c.body.addWidget(durations)
    multi = ChipGroup(["Phone", "Social media", "Messages", "Email", "Browsing", "Daydreaming", "Other"], exclusive=False, size="sm")
    multi.set_values(["Messages", "Browsing"])
    multi._hover_index = 0  # noqa: SLF001 - simulate hover
    c.body.addWidget(_label("ChipGroup · multi, sm, wrapping"))
    c.body.addWidget(multi)
    chips = [Chip("Work deeply", icon="target"), Chip("Read 10 pages"), _hover(Chip("Move your body"))]
    chips[1].setChecked(True)
    c.body.addWidget(_row(*chips, spacing=8))
    seg = SegmentedControl(["7 days", "30 days", "12 weeks"], value="30 days")
    seg2 = SegmentedControl([("system", "System"), ("dark", "Dark"), ("light", "Light")], value="dark", size="sm")
    c.body.addWidget(_row(seg, seg2, spacing=12))
    c.body.addWidget(_label("DayChips · weekdays"))
    c.body.addWidget(DayChips(0b0011111))
    return c


def sec_toggles() -> Card:
    c = Card("Toggles & checkboxes")
    t_off, t_on = Toggle(False), Toggle(True)
    t_dis = Toggle(True)
    t_dis.setEnabled(False)
    t_hover = _hover(Toggle(False))
    c.body.addWidget(_row(t_off, t_on, t_hover, t_dis, spacing=16))
    cb = [Checkbox(), Checkbox(checked=True), Checkbox(True), Checkbox(True, checked=True), Checkbox(True, box=28), Checkbox(True, box=28, checked=True)]
    dashed = Checkbox(True, box=28)
    dashed.set_dashed(True)
    c.body.addWidget(_row(*cb, dashed, spacing=12))
    c.body.addWidget(Checkbox(text="Mark ‘Write chapter 3’ done", checked=True))
    return c


def sec_inputs() -> Card:
    c = Card("Inputs")
    plan = TextField("What will you focus on?", icon="target", height=52)
    c.body.addWidget(plan)
    filled = TextField("Project", height=40)
    filled.set_text("Madhura")
    filled.set_leading_color(theme.project_color("Madhura"))
    filled.setFixedWidth(200)
    hover = TextField("Search…", icon="search")
    hover.frame._hover = True  # noqa: SLF001 - simulate hover
    c.body.addWidget(_row(filled, hover, spacing=10, stretch=False))
    err = TextField("What will you focus on?", icon="target")
    err.set_error("Name the one thing you’ll work on")
    c.body.addWidget(err)
    dis = TextField("Disabled")
    dis.setEnabled(False)
    c.body.addWidget(dis)
    ta = TextArea("Optional note — what went well?")
    c.body.addWidget(ta)
    c.body.addWidget(_row(TimeField("09:00"), TimeField("18:30", use_24h=False), spacing=10))
    c.body.addWidget(_row(Stepper(5, 240, 5, 35, suffix=" min"), Stepper(0.5, 12, 0.5, 2, suffix=" h"), spacing=10))
    sl = Slider(0, 100, 40)
    sl.setFixedWidth(220)
    sel = Select([(25, "25 minutes"), (45, "45 minutes"), (60, "1 hour"), (90, "90 minutes")], 60)
    c.body.addWidget(_row(sel, sl, spacing=16))
    focused = TextField("Capture a stray thought — deal with it later", icon="note")
    focused.frame.is_focused = lambda: True  # type: ignore[method-assign] - simulate focus
    c.body.addWidget(focused)
    return c


def sec_labels() -> Card:
    c = Card("Badges, keys, headers")
    c.body.addWidget(
        _row(
            Badge("25m"),
            _hover(Badge("45m", clickable=True)),
            Badge("Auto", "accent"),
            Badge("Completed", "success", icon="check"),
            Badge("ended early", "warning"),
            spacing=6,
        )
    )
    c.body.addWidget(
        _row(
            Badge("abandoned", "danger"),
            Badge("Break", "break", icon="coffee"),
            Badge("Madhura", dot=theme.project_color("Madhura")),
            Badge("Reading", dot=theme.project_color("Reading")),
            Badge("Health", dot=theme.project_color("Health")),
            spacing=6,
        )
    )
    c.body.addWidget(_row(Kbd("Ctrl+H"), Kbd("Space"), Kbd("Ctrl+Shift+F"), Kbd("?"), spacing=10))
    c.body.addWidget(SectionHeader("Morning", 3, LinkButton("Edit")))
    return c


def sec_rings() -> Card:
    c = Card("Progress rings")
    big = ProgressRing(280, 12)
    big.set_progress(0.62)
    big.set_text("24:59")
    big.set_caption("of 60 min")
    paused = ProgressRing(160, 8)
    paused.set_progress(0.35)
    paused.set_color(theme.palette().text_subtle)
    paused.set_text_color(theme.palette().text_muted)
    paused.set_center_font(fonts.display_font(34, 300))
    paused.set_text("12:40")
    paused.set_caption("Paused")
    brk = ProgressRing(160, 8)
    brk.set_progress(0.8)
    brk.set_color(theme.palette().break_)
    brk.set_center_font(fonts.display_font(34, 300))
    brk.set_text("4:12")
    brk.set_caption("Break")
    smalls = []
    for v in (0.0, 0.25, 0.6, 1.0):
        r = ProgressRing(40, 4)
        r.set_progress(v)
        smalls.append(r)
    done = ProgressRing(40, 4)
    done.set_progress(1.0)
    done.set_color(theme.palette().success)
    tiny = ProgressRing(18, 2.5)
    tiny.set_progress(0.7)
    side = _col(paused, brk, spacing=16)
    c.body.addWidget(_row(big, side, spacing=24))
    c.body.addWidget(_row(*smalls, done, tiny, spacing=12))
    return c


def sec_banners() -> Card:
    c = Card("Inline banners")
    c.body.addWidget(InlineBanner("info", "Nudges snoozed until 14:30", [("Resume", lambda: None)]))
    c.body.addWidget(
        InlineBanner("warning", "You were away 1h 52m", [("Resume", lambda: None), ("Finish (save 23 min)", lambda: None), ("Abandon", lambda: None)])
    )
    c.body.addWidget(InlineBanner("success", "Daily goal reached — 2h focused", dismissible=True))
    c.body.addWidget(
        InlineBanner(
            "accent",
            "You’re between blocks. Plan the next one — or snooze.",
            [("Start ‘Write chapter 3’ · 25m", lambda: None), ("Snooze", lambda: None)],
            dismissible=True,
        )
    )
    return c


def sec_list() -> Card:
    c = Card("Up next", action=Button("Add", "ghost", icon="plus", size="sm"), padding=(12, 16, 12, 12))
    c.title_label().setContentsMargins(8, 0, 0, 0)  # type: ignore[union-attr]
    items = [
        ("Write chapter 3", "25m", False, "normal"),
        ("Review pull requests", "45m", False, "hover"),
        ("Email Priya about the launch plan and the timeline for next week", "15m", False, "selected"),
        ("Ship the habit editor", "60m", True, "normal"),
    ]
    for title, mins, done, state in items:
        row = ListRow(40, separator=False)
        cb = Checkbox(True, checked=done)
        row.add_leading(cb)
        lab = row.set_title(title, "body")
        if done:
            lab.set_tone("muted")
        row.add_trailing(Badge(mins))
        row.add_trailing(IconButton("play", "Start", 28), hover_only=True)
        row.add_trailing(IconButton("more", "More", 28), hover_only=True)
        if state == "hover":
            _hover(row)
        elif state == "selected":
            row.set_selected(True)
        c.body.addWidget(row)
    c.body.setSpacing(2)
    today = ListRow(44, separator=True)
    t = Caption("09:30")
    t.setFixedWidth(44)
    today.add_leading(t)
    today.set_title("Deep work: API design", "label")
    today.set_subtitle("45m · 86% focused")
    c.body.addWidget(today)
    return c


def sec_settings() -> SettingsGroup:
    g = SettingsGroup("Check-ins", "A quiet nudge every few minutes to keep you honest.")
    g.add("Check-ins", "Ask whether you’re still on task.", Toggle(True))
    g.add("Interval", None, Select([(5, "5 min"), (7, "7 min"), (10, "10 min")], 7))
    g.add("Space them out", "When you’re consistently focused.", Toggle(False))
    g.add("Nudges", "Gentle: a quiet panel. Firm: brings Madhura forward.", SegmentedControl(["Off", "Gentle", "Firm"], "Gentle", size="sm"))
    g.add("Idle minutes", None, Stepper(1, 60, 1, 5))
    g.add("Work days", None, DayChips(31))
    return g


def sec_empty() -> Card:
    c = Card()
    es = EmptyState(
        "sparkle",
        "Small habits, big results",
        "Improve by 1% every day — by the end of the year, you’ll reach ~37x growth.",
        Button("New habit", "primary", icon="plus"),
    )
    chips = QWidget()
    cl = QHBoxLayout(chips)
    cl.setContentsMargins(0, 0, 0, 0)
    cl.setSpacing(8)
    for t in ("Ship something small", "Read 10 pages", "Move your body"):
        cl.addWidget(Chip(t, checkable=False, size="sm"))
    es.add_widget(chips)
    c.body.addWidget(es)
    return c


def _checkin_content() -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    lay.addWidget(Micro("Check-in"))
    t = QLabel("Still on Write chapter 3?")
    t.setFont(fonts.font(16, 600))
    lay.addWidget(t)
    lay.addWidget(Caption("23 min in · 22 left"))
    lay.addSpacing(12)
    lay.addWidget(_row(Button("Focused", "primary", size="md"), Button("Focusing back", "secondary"), spacing=8))
    return w


def sec_overlays(host_window: QWidget) -> Card:
    c = Card("Overlays", "FloatingPanel · Menu · Sheet · Toast")
    panel = FloatingPanel("checkin")
    panel.set_content(_checkin_content())
    panel.resize(panel.width(), panel.desired_height())
    panel_snap = _snapshot(panel)
    menu = Menu()
    menu.add_action("Pause", icon="pause", shortcut_text="Space")
    menu.add_action("+5 min", icon="plus")
    menu.add_action("Finish session", icon="check", shortcut_text="Ctrl+Shift+F")
    menu.add_separator()
    menu.add_action("Show seconds", checkable=True, checked=True)
    menu.add_action("Abandon session…", icon="trash", danger=True)
    menu.adjustSize()
    menu.resize(menu.sizeHint())
    menu_snap = _snapshot(menu)

    pop_win = _Backdrop()
    pop_win.setFixedSize(420, 210)
    pl = QVBoxLayout(pop_win)
    pl.setContentsMargins(20, 20, 20, 20)
    chips = ChipGroup([(25, "25m"), (60, "1h"), (90, "90m"), ("custom", "35m")], value="custom")
    pl.addWidget(chips)
    pl.addStretch(1)
    pop_body = QWidget()
    pb = QVBoxLayout(pop_body)
    pb.setContentsMargins(0, 0, 0, 0)
    pb.setSpacing(8)
    pb.addWidget(Micro("Custom length"))
    pb.addWidget(Stepper(5, 240, 5, 35, suffix=" min"))
    pop_win.show()
    QApplication.processEvents()
    pop = Popover(chips, pop_body, anchor_rect=chips.chip_rect("custom"))
    pop.open()
    QApplication.processEvents()
    pop_snap = _snapshot(pop_win)
    pop.dismiss()
    pop_win.hide()

    dlg_sheet = ConfirmSheet("Quit and pause your session?", "Your session will be paused and saved.", "Quit")
    dlg = StyledDialog(None, dlg_sheet)
    dlg_snap = _snapshot(dlg)
    c.body.addWidget(_row(panel_snap, menu_snap, pop_snap, dlg_snap, spacing=16))

    dummy = _Backdrop()
    dummy.setFixedSize(620, 380)
    dl = QVBoxLayout(dummy)
    dl.setContentsMargins(28, 24, 28, 24)
    dl.addWidget(PageHeader("Settings", [Button("Export", "secondary", icon="download")], subtitle="Everything applies instantly."))
    ph = Card("Data")
    ph.body.addWidget(Caption("Open data folder · Export everything · Restore from backup"))
    dl.addWidget(ph)
    dl.addStretch(1)
    host = SheetHost(dummy)
    sheet = ConfirmSheet(
        "Reset all data?",
        "This deletes every session, task and habit. A backup is made first.",
        "Reset everything",
        danger=True,
        type_to_confirm="reset",
    )
    host.show_sheet(sheet)
    toast = ToastHost(dummy)
    toast.show("Session discarded", "info", "Undo", lambda: None)
    dummy.show()
    QApplication.processEvents()
    c.body.addWidget(_snapshot(dummy))
    dummy.hide()
    c._keep = (panel, menu, dummy, host, toast, pop_win, pop, dlg)  # type: ignore[attr-defined]
    toast2_win = _Backdrop()
    toast2_win.setFixedSize(620, 84)
    t2 = ToastHost(toast2_win)
    t2.show("Daily goal reached — 2h focused", "success")
    toast2_win.show()
    QApplication.processEvents()
    c.body.addWidget(_snapshot(toast2_win))
    toast2_win.hide()
    c._keep2 = (toast2_win, t2)  # type: ignore[attr-defined]
    return c


# --------------------------------------------------------------------------- gallery


def build_gallery() -> tuple[QWidget, dict[str, QWidget], list[Callable[[], None]]]:
    """Build the gallery window. Returns (window, sections by name, post-show hooks)."""
    win = _Backdrop()
    win.setWindowTitle("Madhura UI gallery")
    grid = QVBoxLayout(win)
    grid.setContentsMargins(32, 28, 32, 32)
    grid.setSpacing(20)
    cols = QHBoxLayout()
    cols.setSpacing(20)
    header = PageHeader(
        "Madhura UI kit",
        [Button("Light", "secondary", icon="sun"), Button("New habit", "primary", icon="plus")],
        subtitle="Plan the hour. Stay with it.",
    )
    grid.addWidget(header)
    grid.addLayout(cols)
    sections: dict[str, QWidget] = {}
    col0 = [("type", sec_type()), ("buttons", sec_buttons()), ("icon_buttons", sec_icon_buttons()), ("labels", sec_labels()), ("banners", sec_banners())]
    col1 = [("inputs", sec_inputs()), ("chips", sec_chips()), ("toggles", sec_toggles()), ("list", sec_list())]
    col2 = [("rings", sec_rings()), ("settings", sec_settings()), ("empty", sec_empty())]
    for ci, col in enumerate((col0, col1, col2)):
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(20)
        for name, w in col:
            bl.addWidget(w)
            sections[name] = w
        bl.addStretch(1)
        box.setFixedWidth(470 if ci < 2 else 480)
        cols.addWidget(box)
    cols.addStretch(1)
    overlays = sec_overlays(win)
    sections["overlays"] = overlays
    grid.addWidget(overlays)
    hooks: list[Callable[[], None]] = []

    def _focus_rings() -> None:
        btn = getattr(sections["buttons"], "_focus_target", None)
        if btn is not None:
            btn.setFocus(Qt.FocusReason.TabFocusReason)
            show_focus_ring(btn, 8.0)

    hooks.append(_focus_rings)
    return win, sections, hooks


def _fit(win: QWidget) -> None:
    """Size the top-level gallery honouring height-for-width (word-wrapped labels, chips)."""
    win.adjustSize()
    QApplication.processEvents()
    lay = win.layout()
    if lay is not None and lay.hasHeightForWidth():
        win.resize(win.width(), lay.totalHeightForWidth(win.width()))
    QApplication.processEvents()


def render(out: Path, themes: list[str], sections: bool, suffix: str = "") -> list[Path]:
    """Render the gallery for each theme into ``out``; return written paths."""
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    fonts.apply_app_font(app)  # type: ignore[arg-type]
    tm = theme.ThemeManager(themes[0], "indigo", reduce_motion=True)
    tm.apply(app)  # type: ignore[arg-type]
    out.mkdir(parents=True, exist_ok=True)
    win, secs, hooks = build_gallery()
    win.show()
    _fit(win)
    written: list[Path] = []
    for name in themes:
        tm.set_theme(name)
        QApplication.processEvents()
        for h in hooks:
            h()
        QApplication.processEvents()
        # The overlay previews are snapshots: rebuild them for this theme.
        old = secs["overlays"]
        grid: QVBoxLayout = win.layout()  # type: ignore[assignment]
        grid.removeWidget(old)
        old.deleteLater()
        fresh = sec_overlays(win)
        secs["overlays"] = fresh
        grid.addWidget(fresh)
        _fit(win)
        for h in hooks:
            h()
        QApplication.processEvents()
        path = out / f"gallery_{name}{suffix}.png"
        win.grab().save(str(path))
        written.append(path)
        if sections:
            sdir = out / "sections"
            sdir.mkdir(parents=True, exist_ok=True)
            for sname, w in secs.items():
                pm: QPixmap = win.grab(QRect(w.mapTo(win, QPoint(0, 0)), w.size()).adjusted(-12, -12, 12, 12))
                p = sdir / f"{sname}_{name}{suffix}.png"
                pm.save(str(p))
    win.hide()
    return written


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--themes", default="dark,light")
    ap.add_argument("--sections", action="store_true")
    ap.add_argument("--no-scaled", action="store_true")
    ap.add_argument("--suffix", default="")
    args = ap.parse_args(argv)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    themes = [t.strip() for t in args.themes.split(",") if t.strip()]
    paths = render(args.out, themes, args.sections, args.suffix)
    for p in paths:
        print(p)
    if not args.no_scaled:
        env = dict(os.environ, QT_SCALE_FACTOR="1.5", QT_QPA_PLATFORM=os.environ.get("QT_QPA_PLATFORM", "offscreen"))
        cmd = [sys.executable, str(Path(__file__).resolve()), "--out", str(args.out), "--themes", "dark", "--no-scaled", "--suffix", "_1.5x"]
        if args.sections:
            cmd.append("--sections")
        subprocess.run(cmd, env=env, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
