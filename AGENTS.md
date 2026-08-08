# Supervertaler – AI Agent Reference

Concise project reference for AI-assisted development. Check `CHANGELOG.md` for latest version and recent work.

## Project Overview

Supervertaler is a desktop AI-enhanced translation workbench built with PyQt6. It combines AI translation (multiple LLM providers), translation memory, terminology management, and quality assurance in a single application.

**Removed features (do NOT reference):**
- CAT tool integrations (Trados, memoQ, CafeTran, Phrase, Déjà Vu)
- Voice Commands and Voice Dictation
- macOS support

**Target platform:** Windows (portable Python 3.12, no venv)

## Key Paths

| What | Path |
| --- | --- |
| Main app (monolithic, ~72k lines) | `Supervertaler.py` |
| Modules | `modules/` |
| Version | Hardcoded in `Supervertaler.py` (`__version__`) |
| Changelog | `CHANGELOG.md` |
| Tests | `tests/` |
| Settings | `settings/settings.json` |
| Dependencies | `requirements.txt` |

## Important Modules

- `modules/termlens_widget.py` – TermLens inline terminology display (`TermLensWidget`)
- `modules/llm_clients.py` – LLM provider abstraction
- `modules/termbase_manager.py` – glossary/termbase CRUD
- `modules/database_manager.py` – SQLite database layer
- `modules/translation_results_panel.py` – match panel UI
- `modules/shortcut_manager.py` – keyboard shortcut system
- `modules/simple_segmenter.py` – sentence segmentation (`SimpleSegmenter`, `MarkdownSegmenter`)
- `modules/platform_helpers.py` – cross-platform utilities (Windows/Linux)
- `modules/chat_view_widget.py` – AI chat widget
- `modules/config_manager.py` – configuration management
- `modules/theme_manager.py` – UI theme management

## Removed Modules (DO NOT IMPORT)

### CAT Tools (removed):
- `modules/cafetran_docx_handler.py`
- `modules/mqxliff_handler.py`
- `modules/sdlppx_handler.py`
- `modules/sdltm_handler.py`

### Voice Commands (removed):
- `modules/autostart.py`
- `modules/mic_devices.py`
- `modules/voice_command_dialog.py`
- `modules/voice_commands.py`
- `modules/voice_dictation.py`
- `modules/voice_dictation_lite.py`
- `modules/voice_hotkey_listener.py`
- `modules/voice_release_poller.py`
- `modules/voice_tab.py`
- `modules/voice_vocabulary.py`
- `modules/vosk_model_manager.py`
- `autostart.py`
- `mic_devices.py`

## Portable Python Environment

- **Python version:** 3.12 embedded (portable, no venv)
- **Install dependencies:** `python -m pip install -r requirements.txt`
- **Run app:** `python Supervertaler.py`
- **Critical:** `sys.path` fix at the top of `Supervertaler.py` is REQUIRED for portable Python. Do NOT remove it.

## Settings Architecture

Primary config: `settings/settings.json` with top-level sections: `api_keys`, `general`, `ui`, `features`.

Satellite files under `settings/`: `themes.json`, `shortcuts.json`, `recent_projects.json`, `find_replace_history.json`, `superlookup_history.json`, `model_version_cache.json`.

Legacy settings files are auto-migrated at startup and renamed to `.migrated`.

## Pitfalls

- `Supervertaler.py` is ~72,000 lines – **ALWAYS read/edit by line range, NEVER full-file** - file Supervertaler_outline.txt.
- Qt table access: use `cellWidget()` for editors, `item()` for plain items.
- Block signals during programmatic text updates to avoid cascades.
- Style issues can be timing-related (hidden widgets, deferred visibility).
- Do NOT import any module listed in "Removed Modules" section.
- Portable/embedded Python does NOT add script directory to `sys.path` automatically – the fix at the top of `Supervertaler.py` is mandatory.

## Testing

```bash
pytest tests/

Manual smoke test: import DOCX → translate → export, save/load `.svproj`, TM + termbase matching, AI translation.
