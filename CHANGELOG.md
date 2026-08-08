08.-8.2026
Delete CAT Tools module in /modules 
sdlppx_handler.py (2,270) - Trados SDLPPX/SDLRPX
mqxliff_handler.py (717) - memoQ XLIFF
memoqrtf_handler.py (688) - memoQ RTF
cafetran_docx_handler.py (379) - CafeTran
phrase_docx_handler.py (669) - Phrase
dejavurtf_handler.py (784) - Déjà Vu RTF
trados_docx_handler.py (433) - Trados DOCX
trados_bridge_client.py (524) - Trados Bridge API
sdltm_handler.py

Delete Voice Commands module in /modules 
voice_commands.py (1,465)
voice_tab.py (1,378)
voice_dictation.py (497)
voice_dictation_lite.py (321)
voice_hotkey_listener.py (480)
voice_release_poller.py (310)
voice_command_dialog.py (355)
voice_vocabulary.py 
autostart.py
mic_devices.py


Delete in Supervertaler.py:

# Version Information – read from pyproject.toml (single source of truth)
def _read_version():
    """Read version from pyproject.toml, importlib.metadata, or hardcoded fallback."""
    import os as _os
    # 1. Try pyproject.toml (works in dev mode and PyInstaller builds that bundle it)
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # Python < 3.11
        _toml_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "pyproject.toml")
        with open(_toml_path, "rb") as f:
            _data = tomllib.load(f)
        return _data["project"]["version"]
    except Exception:
        pass
    # 2. Try importlib.metadata (works for pip install)
    try:
        from importlib.metadata import version
        return version("supervertaler")
    except Exception:
        pass
    # 3. Last-resort hardcoded fallback
    return "1.10.313"


    # --- macOS Finder crash logging (v1.9.275) ---
# When launched via Finder, stdout/stderr go nowhere; write to Desktop log.
import sys as _sys
import os as _os
if getattr(_sys, 'frozen', False) and _sys.platform == 'darwin':
    _crash_log = _os.path.expanduser('~/Desktop/supervertaler_crash.log')
    try:
        with open(_crash_log, 'w') as _f:
            _f.write(f"Launch time: {__import__('datetime').datetime.now()}\n")
            _f.write(f"Version: {__version__}\n")
            _f.write(f"CWD: {_os.getcwd()}\n")
            _f.write(f"sys.executable: {_sys.executable}\n")
            _f.write(f"sys._MEIPASS: {getattr(_sys, '_MEIPASS', 'N/A')}\n")
            _f.write(f"__file__: {__file__}\n")
            _f.write(f"LANG: {_os.environ.get('LANG', 'NOT SET')}\n")
            _f.write(f"LC_CTYPE: {_os.environ.get('LC_CTYPE', 'NOT SET')}\n")
            _f.write("--- Starting app ---\n")
    except Exception:
        pass
del _sys, _os
# --- end crash logging ---


Added `sys.path` in Supervertaler.py:

# =====================================================================
# FIX: Portable/embedded Python не добавляет директорию скрипта в sys.path.
# Добавляем её вручную, чтобы импорты из modules/ работали.
# =====================================================================
import sys as _sys
import os as _os
_script_dir = _os.path.dirname(_os.path.abspath(__file__))
if _script_dir not in _sys.path:
    _sys.path.insert(0, _script_dir)
del _sys, _os
# =====================================================================


Edit in Supervertaler.py:

__version__ = "0.1.0"
__phase__ = "0.1"
__release_date__ = "2026-08-08"
__edition__ = "Qt"