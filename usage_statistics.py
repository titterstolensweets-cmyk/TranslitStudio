"""
Minimal, opt-in anonymous usage statistics for Supervertaler Workbench.

When the user opts in, a single lightweight ping is sent once per session
on app startup. The payload contains only:
  - A random anonymous ID (UUID, generated locally, not tied to any account)
  - App version
  - OS version
  - Python version
  - System locale
  - Platform (win32, darwin, linux)
  - Architecture (x86_64, arm64, etc.)
  - Display scale, Windows text size, and the in-app UI font scale (percentages)
  - Product identifier ("workbench")

No personal data, no translation content, no termbase info, no tracking.
Silent failure – if the ping fails, nothing happens.
"""

import json
import locale
import platform
import struct
import sys
import threading
import uuid
from pathlib import Path
from typing import Optional


PING_URL = "https://supervertaler-stats.michaelbeijer-co-uk.workers.dev/ping"
PRODUCT = "workbench"
USER_AGENT = "Supervertaler-Workbench/1.0"
TIMEOUT_SECONDS = 10


def send_ping(settings_path: Path, app_version: str) -> None:
    """
    Send the anonymous usage ping if the user has opted in.
    Runs in a background thread – never blocks the UI.
    Call this once during app startup.
    """
    # Capture the display scale on the calling (GUI) thread — querying the Qt
    # screen from the worker thread is not safe.
    display_scale = _get_display_scale()
    thread = threading.Thread(
        target=_send_ping_background,
        args=(settings_path, app_version, display_scale),
        daemon=True,
        name="UsageStatsPing",
    )
    thread.start()


def _send_ping_background(settings_path: Path, app_version: str,
                         display_scale: str = "unknown") -> None:
    """Background worker – sends the ping. All exceptions swallowed silently."""
    try:
        settings = _load_settings(settings_path)

        # Only send if explicitly opted in
        if not settings.get("usage_statistics_enabled", False):
            return

        # Get or create the anonymous ID
        anonymous_id = settings.get("usage_statistics_id", "")
        if not anonymous_id:
            anonymous_id = str(uuid.uuid4())
            settings["usage_statistics_id"] = anonymous_id
            _save_settings(settings_path, settings)

        payload = {
            "id": anonymous_id,
            "product": PRODUCT,
            "app_version": app_version,
            "os_version": _get_os_version(),
            "python_version": _get_python_version(),
            "locale": _get_locale(),
            "platform": sys.platform,
            "arch": _get_arch(),
            "display_scale": display_scale,
            "text_scale": _get_text_scale(),
            "ui_scale": _get_ui_scale(settings),
        }

        # Use urllib to avoid requiring 'requests' just for this
        import urllib.request
        import urllib.error

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            PING_URL,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS)

    except Exception:
        # Silent failure – no retries, no queuing, no error messages
        pass


def is_opted_in(settings_path: Path) -> bool:
    """Check if the user has opted in to usage statistics."""
    try:
        settings = _load_settings(settings_path)
        return settings.get("usage_statistics_enabled", False)
    except Exception:
        return False


def has_been_asked(settings_path: Path) -> bool:
    """Check if the user has already been asked about usage statistics."""
    try:
        settings = _load_settings(settings_path)
        return settings.get("usage_statistics_asked", False)
    except Exception:
        return False


def set_opted_in(settings_path: Path, enabled: bool) -> None:
    """Set the user's opt-in preference."""
    try:
        settings = _load_settings(settings_path)
        settings["usage_statistics_enabled"] = enabled
        settings["usage_statistics_asked"] = True
        if enabled and not settings.get("usage_statistics_id"):
            settings["usage_statistics_id"] = str(uuid.uuid4())
        _save_settings(settings_path, settings)
    except Exception:
        pass


def show_opt_in_dialog(parent=None) -> bool:
    """
    Show the one-time opt-in dialog. Returns True if user opted in.
    Must be called from the Qt main thread.
    """
    from PyQt6.QtWidgets import QMessageBox, QLabel
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QDesktopServices
    from PyQt6.QtCore import QUrl

    msg = QMessageBox(parent)
    msg.setWindowTitle("Supervertaler Workbench")
    msg.setIcon(QMessageBox.Icon.Question)
    msg.setText("<b>Help improve Supervertaler</b>")
    msg.setInformativeText(
        "Would you like to share anonymous usage statistics to help "
        "improve the app?\n\n"
        "Only the following is sent – once per session, on startup:\n"
        "  •  App version\n"
        "  •  OS and Python version\n"
        "  •  System locale\n\n"
        "No personal data, translation content, or termbase info is "
        "ever collected. You can change this at any time in Settings."
    )

    yes_btn = msg.addButton("Yes, share statistics", QMessageBox.ButtonRole.YesRole)
    no_btn = msg.addButton("No thanks", QMessageBox.ButtonRole.NoRole)
    msg.setDefaultButton(yes_btn)

    msg.exec()
    return msg.clickedButton() == yes_btn


# ── Internal helpers ──────────────────────────────────────────────


def _load_settings(settings_path: Path) -> dict:
    """Load the unified settings file and return the top-level dict."""
    if not settings_path.exists():
        return {}
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Usage stats settings are stored at the top level of settings.json
        # alongside the section dicts (api_keys, general, ui, features)
        return data
    except Exception:
        return {}


def _save_settings(settings_path: Path, settings: dict) -> None:
    """Save updated settings back to the unified settings file."""
    try:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _get_os_version() -> str:
    """Return a human-readable OS version string."""
    try:
        system = platform.system()
        if system == "Windows":
            ver = platform.version()
            release = platform.release()
            return f"Windows {release} ({ver})"
        elif system == "Darwin":
            ver = platform.mac_ver()[0]
            return f"macOS {ver}" if ver else "macOS (unknown)"
        elif system == "Linux":
            # Try to get distro info
            try:
                import distro
                return f"Linux {distro.name()} {distro.version()}"
            except ImportError:
                return f"Linux {platform.release()}"
        return f"{system} {platform.release()}"
    except Exception:
        return "unknown"


def _get_python_version() -> str:
    """Return the Python version string."""
    try:
        return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    except Exception:
        return "unknown"


def _get_locale() -> str:
    """Return the system locale."""
    try:
        loc = locale.getdefaultlocale()[0]
        return loc if loc else "unknown"
    except Exception:
        return "unknown"


def _get_arch() -> str:
    """Return the process architecture."""
    try:
        # Use struct to detect 32-bit vs 64-bit Python
        bits = struct.calcsize("P") * 8
        machine = platform.machine().lower()
        return f"{machine}_{bits}bit" if machine else f"{bits}bit"
    except Exception:
        return "unknown"


def _get_display_scale() -> str:
    """Display scale as a percentage string (e.g. '175'; '200' on a Retina
    screen), from the Qt primary screen's device pixel ratio. Must be called
    from the GUI thread."""
    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            screen = app.primaryScreen()
            if screen is not None:
                pct = int(round(screen.devicePixelRatio() * 100))
                if 50 <= pct <= 600:
                    return str(pct)
    except Exception:
        pass
    return "unknown"


def _get_text_scale() -> str:
    """Windows accessibility 'Text size' (%). Windows-only; 'unknown' elsewhere
    (the setting doesn't exist on macOS/Linux)."""
    if sys.platform != "win32":
        return "unknown"
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Accessibility") as key:
            val, _ = winreg.QueryValueEx(key, "TextScaleFactor")
            pct = int(val)
            return str(pct) if 50 <= pct <= 600 else "unknown"
    except FileNotFoundError:
        return "100"  # key/value absent → Windows default of 100%
    except Exception:
        return "unknown"


def _get_ui_scale(settings: dict) -> str:
    """In-app Workbench UI font scale (%) – the 'Global UI Font Scale' setting.
    Defaults to 100 when unset."""
    try:
        for container in (settings.get("general", {}), settings, settings.get("ui", {})):
            if isinstance(container, dict) and "global_ui_font_scale" in container:
                pct = int(container["global_ui_font_scale"])
                return str(pct) if 10 <= pct <= 600 else "unknown"
        return "100"
    except Exception:
        return "unknown"
