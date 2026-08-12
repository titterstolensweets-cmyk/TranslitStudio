"""
Cross-platform "Start with computer" helper for Supervertaler Workbench.

Provides ``is_enabled()`` / ``enable()`` / ``disable()`` for Windows, macOS,
and Linux without requiring elevation:

  * Windows:  HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
  * macOS:    ~/Library/LaunchAgents/com.supervertaler.workbench.plist
  * Linux:    ~/.config/autostart/supervertaler.desktop  (FreeDesktop autostart)

The launch command is computed from the *currently running* interpreter:

  * Frozen .exe / .app  → ``sys.executable`` directly.
  * Dev runs from source → ``pythonw.exe`` (or platform GUI variant) plus the
    absolute path to ``Supervertaler.py``. This matches how
    ``run-silent.cmd`` launches the app, so the auto-started instance has
    no console window.

All functions return False / silently no-op on unsupported platforms, so
callers can wire them up unconditionally.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Tuple

APP_NAME = "Supervertaler"
APP_BUNDLE_ID = "com.supervertaler.workbench"

# ── Resolving the launch command ────────────────────────────────────────


def get_launch_command() -> Tuple[str, List[str]]:
    """Return ``(executable, args)`` to register with the OS at boot.

    For source runs we deliberately pick the GUI Python variant
    (``pythonw.exe`` on Windows, ``python`` elsewhere) so the auto-started
    process never opens a terminal window.
    """
    if getattr(sys, "frozen", False):
        return sys.executable, []

    py = Path(sys.executable)
    if sys.platform == "win32":
        gui_py = py.with_name("pythonw.exe")
        if not gui_py.exists():
            gui_py = py
    else:
        gui_py = py

    # Resolve Supervertaler.py – this module lives in modules/, so go one up.
    script = (Path(__file__).resolve().parent.parent / "Supervertaler.py").resolve()
    return str(gui_py), [str(script)]


def _command_string_windows(executable: str, args: List[str]) -> str:
    """Build a single quoted command line for the Windows Run registry key."""
    parts = [f'"{executable}"'] + [f'"{a}"' for a in args]
    return " ".join(parts)


# ── Windows ─────────────────────────────────────────────────────────────

_WIN_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _windows_is_enabled() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY) as key:
            try:
                value, _ = winreg.QueryValueEx(key, APP_NAME)
                return bool(value)
            except FileNotFoundError:
                return False
    except OSError:
        return False


def _windows_enable() -> bool:
    try:
        import winreg
        executable, args = get_launch_command()
        cmd_str = _command_string_windows(executable, args)
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd_str)
        return True
    except OSError as e:
        print(f"[Autostart] Windows enable failed: {e}")
        return False


def _windows_disable() -> bool:
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
        return True
    except OSError as e:
        print(f"[Autostart] Windows disable failed: {e}")
        return False


# ── macOS ───────────────────────────────────────────────────────────────


def _macos_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{APP_BUNDLE_ID}.plist"


def _macos_is_enabled() -> bool:
    return _macos_plist_path().exists()


def _macos_enable() -> bool:
    try:
        import plistlib
        executable, args = get_launch_command()
        plist = {
            "Label": APP_BUNDLE_ID,
            "ProgramArguments": [executable] + args,
            "RunAtLoad": True,
            "ProcessType": "Interactive",
        }
        path = _macos_plist_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            plistlib.dump(plist, f)
        return True
    except OSError as e:
        print(f"[Autostart] macOS enable failed: {e}")
        return False


def _macos_disable() -> bool:
    try:
        path = _macos_plist_path()
        if path.exists():
            path.unlink()
        return True
    except OSError as e:
        print(f"[Autostart] macOS disable failed: {e}")
        return False


# ── Linux (FreeDesktop autostart spec) ──────────────────────────────────


def _linux_desktop_path() -> Path:
    return Path.home() / ".config" / "autostart" / "supervertaler.desktop"


def _linux_is_enabled() -> bool:
    return _linux_desktop_path().exists()


def _linux_enable() -> bool:
    try:
        executable, args = get_launch_command()
        # Quote args containing spaces; .desktop's Exec uses backslash-escaping
        # but plain quoting works for the common case.
        def _q(s: str) -> str:
            return f'"{s}"' if " " in s else s
        exec_line = " ".join([_q(executable)] + [_q(a) for a in args])
        body = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={APP_NAME} Workbench\n"
            f"Exec={exec_line}\n"
            "X-GNOME-Autostart-enabled=true\n"
            "Terminal=false\n"
        )
        path = _linux_desktop_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return True
    except OSError as e:
        print(f"[Autostart] Linux enable failed: {e}")
        return False


def _linux_disable() -> bool:
    try:
        path = _linux_desktop_path()
        if path.exists():
            path.unlink()
        return True
    except OSError as e:
        print(f"[Autostart] Linux disable failed: {e}")
        return False


# ── Public dispatch ─────────────────────────────────────────────────────


def is_supported() -> bool:
    """True if this platform has a known autostart mechanism we can use."""
    return sys.platform in ("win32", "darwin", "linux")


def is_enabled() -> bool:
    if sys.platform == "win32":
        return _windows_is_enabled()
    if sys.platform == "darwin":
        return _macos_is_enabled()
    if sys.platform == "linux":
        return _linux_is_enabled()
    return False


def enable() -> bool:
    if sys.platform == "win32":
        return _windows_enable()
    if sys.platform == "darwin":
        return _macos_enable()
    if sys.platform == "linux":
        return _linux_enable()
    return False


def disable() -> bool:
    if sys.platform == "win32":
        return _windows_disable()
    if sys.platform == "darwin":
        return _macos_disable()
    if sys.platform == "linux":
        return _linux_disable()
    return False
