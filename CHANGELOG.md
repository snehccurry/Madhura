# Changelog

All notable changes to Madhura are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [2.0.0] — 2026-10-11

Madhura 2.0 is a complete rebuild. It keeps the original idea (plan the hour, stay with it,
check in every 7 minutes, build habits 1% at a time) and rewrites everything around it in Qt 6
(PySide6), with a new design, honest time tracking, local data you can back up and export, and a
Windows build produced by CI.

### Added

- **Planning:** a *Plan your next hour* card whose heading follows the chosen length. It has 15m–90m presets plus a custom length (5–240 min), an optional project, autocompletion from your history and a live *"Ends at … · check-ins every 7 min"* preview.
- **Up next** list for the day: check off, start, reorder, edit, move to tomorrow, carry over unfinished items from earlier days. The next item is filled in when a block ends.
- **Today** card with each finished block and its focus score, a daily goal ring (2 h by default) and a day streak.
- **Parking lot** for stray thoughts captured mid-session (<kbd>C</kbd>), turned into tasks later.
- Labelled **Break / Lunch / Meeting / Admin** blocks (*"Not a focus block?"*).
- **Session view:** a countdown ring, pause/resume, +5 min (sessions up to 8 hours), finish, abandon and hide to the mini timer.
- **Session summary:** a check-in timeline, a note, *Mark done* for Up next tasks, *Keep going +15* and a 5/10/15-minute break. A non-intrusive completion panel appears when the window is hidden, and the summary waits for your return.
- **Check-ins** that never take keyboard focus: inline on the Focus page or as a small corner panel.
  - Optional drift reasons (Phone, Social media, Messages, Email, Browsing, Daydreaming, Other).
  - Adaptive spacing after consistent focus.
  - Unanswered check-ins count as *missed*, and two misses in a row pause the session.
  - Check-ins wait while you present or run a full-screen app (Windows) and in quiet mode.
- **Away detection** (Windows, macOS): after 5+ idle minutes, choose whether to keep or remove the away time.
- **Mini timer:** a floating pill that expands on hover, can be dragged and snaps to edges, remembers its position per monitor, and shows paused, break and done states.
- **Planning nudges:** *Gentle*, *Firm* or *Off*, only during your work days and hours.
  - One-click start of the next planned task.
  - Snooze, *In a meeting…*, *Done for today*.
  - A morning nudge with the first block you planned.
  - A once-a-day *Wrap up your day?* prompt.
- **Habits**, redesigned around *Atomic Habits*:
  - emoji, cue, time or anytime, and repeat days;
  - grouping by time of day;
  - skip without breaking a streak, current and best streaks, last-7-days dots, *"don't miss twice"*;
  - reminders with Done / Snooze / Skip that wait until a focus block ends (unless urgent);
  - habits that complete themselves from focused time;
  - starter suggestions, archive.
- **Insights** for 7 days, 30 days or 12 weeks:
  - focus time, sessions, focus score and habit completion compared with the previous period;
  - a bar chart with your goal line;
  - a *when you focus best* heatmap;
  - top projects and tasks, and what pulls you away;
  - recent sessions with delete and undo;
  - CSV export.
- **Daily wrap-up:** today against your goal, then move or drop unfinished tasks, turn parking-lot notes into tasks, *one thing that went well*, tomorrow's first block, *Shutdown complete*.
- **Weekly recap** card at the start of each week.
- **Onboarding** in four steps: welcome, your day, accountability style, first block.
- **Settings** page in 10 groups, every change applied instantly.
- **Appearance and accessibility:** dark, light or system theme; five accent colors; reduce motion; 12/24-hour time; accessible names for icon buttons and charts.
- **Keyboard shortcuts** for every action, with a shortcuts sheet (<kbd>?</kbd>).
- **System tray** icon showing session progress, with a full menu: start, pause, +5 min, finish, mini timer, *Quiet for 1 hour*, snooze nudges, wrap up, quit.
- **Launch at login** on Windows and Linux; keep running in the tray; start minimized.
- **Single instance:** launching again brings the running window forward.
- **Local SQLite database** in the per-user data folder:
  - daily backups (the newest 7 are kept) and *Restore from backup*;
  - *Export everything* (JSON);
  - *Reset all data* (with a backup first);
  - automatic recovery from a damaged database.
- **Crash-safe sessions:** the running session is saved every 30 seconds and on every change. After a restart it resumes, or pauses with an *away* notice if the gap was long.
- **Import of 1.x habits** (`habits.json`) during onboarding or from Settings.
- **Windows build** with PyInstaller (one folder, app icon, version resource), built by GitHub Actions and published as a release zip for `v*` tags.
- A hidden **`--self-test [REPORT]`** flag that checks an install or build headlessly.
- **`pip install .`** support with a `madhura` command; `python -m madhura`.
- **Development:** about 1,400 automated tests, CI on Linux (Python 3.10 and 3.13) and Windows, deterministic screenshot (`tools/screenshots.py`) and demo-data (`tools/seed_demo.py`) tools, and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

### Changed

- Rebuilt on **PySide6 / Qt 6** instead of Tkinter, with a new calm, dark-first design. Windows-first, and it also runs on Linux and macOS.
- **Check-ins follow focused time.** Paused time doesn't count toward the 7 minutes, and the interval (5–30 min), the auto-dismiss time and adaptive spacing are configurable.
- **"Forced" planning became configurable nudges.** The 1.x window stayed on top of everything until a session was planned. 2.0 nudges only during work hours, gently by default. *Firm* mode brings the window forward without taking the keyboard.
- **Session lengths:** presets 15/25/45/60/90 min plus custom (1.x offered 10–240 min in a drop-down). *Lock* is now *Lock in*.
- **Habit times** can be any `HH:MM` or *anytime* (1.x used whole hours), and habits can repeat on chosen days.
- **Streaks:** the 1.x "return streak" of consecutive *Focused* answers is replaced by a day streak for meeting your daily goal and per-habit streaks. Drifts are shown as *came back*, never as failures.
- **Data** moved from JSON files next to the app (`madhura_sessions.json`, `habits.json`) to a SQLite database in the per-user data folder (`%LOCALAPPDATA%\Madhura` on Windows).
- **Sound:** one soft chime with a volume control and per-event switches. It stays silent while you present and in quiet mode.
- **Starting with Windows** is a setting (*Launch at login*) instead of a manual Startup-folder shortcut.

### Removed

- The Tkinter, Heema, BlurWindow, tkextrafont, pygame and TinyDB dependencies. The only runtime dependency is `PySide6-Essentials`.
- The 1.x scripts (`madhura.py`, `madhura_with_atomic_habits_concept.py`, `test.py`), the bundled sample data files and the MP3 copy of the notification sound. The git history keeps them.
- Importing 1.x session history (`madhura_sessions.json`). Only habits are imported.

### Fixed

- **Honest time.** Timing is computed from the clock rather than by counting timer ticks, so it doesn't drift. Sleep, suspend, clock changes and time away from the computer are detected and never credited as focus.
- **A running session survives a crash, restart or reboot.** 1.x kept it in memory only.
- **No more focus stealing.** Check-in and reminder panels never take the keyboard, and the window is never forced to stay on top.
- **Data and the notification sound no longer depend on the folder Madhura is started from.** 1.x used paths relative to the working directory, which could break when started from a shortcut or at login.

## [1.x] — March 2026

The original Tkinter app for Windows, released in three builds:

- **[3.29.2026](https://github.com/snehccurry/Madhura/releases/tag/Madhura-3.29.2026)**: the
  *Atomic Habits* page. Daily habits with a time and a reminder (*Done* / *Skip*), suggestions
  and the *"Improve by 1% every day"* motto.
- **[3.10.2026](https://github.com/snehccurry/Madhura/releases/tag/version-3.10.2026)**: a
  frameless window without the title bar, a hide button, and a fix for a flickering glitch.
- **[3.7.2026](https://github.com/snehccurry/Madhura/releases/tag/version-3.7.2026)**: the
  first release.
  - *Plan your next hour*: name the task, choose the minutes (60 by default) and **Lock** it. The
    window stayed on top until a session was planned.
  - A countdown with a draggable, hover-to-expand **focus pill** while the window was minimized.
  - **Check-ins every 7 minutes**, *Focused* / *Focusing back*, with a notification sound, a
    return streak and a drift count.
  - A **today's focus** summary of completed minutes.

Data lived in TinyDB JSON files (`madhura_sessions.json`, `habits.json`) next to the app.

[2.0.0]: https://github.com/snehccurry/Madhura/compare/Madhura-3.29.2026...v2.0.0
[1.x]: https://github.com/snehccurry/Madhura/releases/tag/Madhura-3.29.2026
