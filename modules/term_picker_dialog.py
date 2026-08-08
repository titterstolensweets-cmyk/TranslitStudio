"""
term_picker_dialog.py
─────────────────────

Modal dialog listing all matched terms for the current segment as a
tabular grid (#, source, target, termbase). Mirrors the Trados
``TermPickerDialog`` (see
``Supervertaler-for-Trados/src/Supervertaler.Trados/Controls/TermPickerDialog.cs``)
in feature set: synonym expand/collapse, 0-9 quick-pick, pink/blue/yellow
row colours, persisted size + column widths.

Triggered by Ctrl+Shift+P (Workbench, v1.10.206+) — matches the Trados
plugin convention exactly. (Originally bound to Ctrl+Shift+P in v1.10.87;
moved to Ctrl+Shift+B in v1.10.89 because Scratchpad owned Ctrl+Shift+P;
reclaimed Ctrl+Shift+P in v1.10.206 once Scratchpad was moved to Alt+S.)

Usage
─────
    matches = build_term_picker_matches(segment)
    dlg = TermPickerDialog(matches, settings=…, parent=main_window)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        target = dlg.selected_target_term
        if target:
            insert_into_target_cell(target)

``matches`` is a list of dicts:
    {
        "index":              1-based number shown in the # column,
        "source_text":        the source token / phrase,
        "primary": {
            "target_term":    canonical target,
            "termbase_name":  termbase label,
            "is_project_termbase": bool,
            "is_nontranslatable":  bool,
        },
        "synonyms": [         optional — collapsed by default
            {
                "target_term": …,
                "termbase_name": …,
            },
            …
        ],
    }
"""

from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QKeyEvent
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


# ── Colour palette ─────────────────────────────────────────────────────
# Light pastel backgrounds matching the TermLens chip colours. Kept on
# the dialog instead of in a shared module so a future visual tweak is
# scoped to the picker without touching the chip rendering.
_BG_PROJECT = QColor("#FFE5F0")  # pink — project termbase (priority 1)
_BG_REGULAR = QColor("#D6EBFF")  # blue — other termbases
_BG_NT      = QColor("#FFF3D0")  # amber — non-translatable
_BG_SUB     = QColor("#F5F5FA")  # grey — synonym sub-rows
_FG_SUB     = QColor("#3C3C3C")  # darker text for sub-rows


# ── Dialog ─────────────────────────────────────────────────────────────


class TermPickerDialog(QDialog):
    """Modal term-picker grid with collapsible synonym rows.

    Public API after exec():
        ``selected_target_term`` — the text to insert (None if cancelled).
    """

    def __init__(
        self,
        matches: List[dict],
        settings: Optional[object] = None,
        parent: Optional[QWidget] = None,
        source_lang_label: str = "Source",
        target_lang_label: str = "Target",
    ):
        super().__init__(parent)
        self._matches = matches or []
        self._settings = settings
        self.selected_target_term: Optional[str] = None

        self.setWindowTitle("TermPicker")
        self.setModal(True)
        self.setMinimumSize(420, 260)
        self.resize(620, 420)

        # Restore persisted size from settings dict, if provided. We use
        # a plain dict-style settings holder rather than a Qt-specific
        # class so the same object can be persisted to JSON alongside
        # the rest of the user-config blob.
        if isinstance(self._settings, dict):
            w = self._settings.get('term_picker_width')
            h = self._settings.get('term_picker_height')
            if isinstance(w, int) and isinstance(h, int) and w > 200 and h > 150:
                self.resize(w, h)

        self._build_ui(source_lang_label, target_lang_label)
        self._populate()

        # Restore column widths AFTER populate so the columns exist.
        if isinstance(self._settings, dict):
            widths = self._settings.get('term_picker_column_widths')
            if isinstance(widths, list) and len(widths) == self._tree.columnCount():
                for i, w in enumerate(widths):
                    if isinstance(w, int) and 30 <= w <= 800:
                        self._tree.setColumnWidth(i, w)

    # ── Construction ────────────────────────────────────────────────────

    def _build_ui(self, src_label: str, tgt_label: str):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 8)
        outer.setSpacing(6)

        self._tree = QTreeWidget(self)
        self._tree.setRootIsDecorated(False)  # we draw our own ▸/▾ in the # column
        self._tree.setUniformRowHeights(True)
        self._tree.setAlternatingRowColors(False)
        self._tree.setColumnCount(4)
        self._tree.setHeaderLabels(["#", src_label, tgt_label, "Termbase"])
        self._tree.header().setStretchLastSection(False)
        self._tree.setColumnWidth(0, 56)
        self._tree.setColumnWidth(1, 170)
        self._tree.setColumnWidth(2, 210)
        self._tree.setColumnWidth(3, 150)
        self._tree.setSelectionMode(self._tree.SelectionMode.SingleSelection)
        self._tree.setSelectionBehavior(self._tree.SelectionBehavior.SelectRows)
        self._tree.itemDoubleClicked.connect(lambda *_: self._accept_current())
        # Capture Enter / arrows on the tree itself.
        self._tree.installEventFilter(self)
        outer.addWidget(self._tree, stretch=1)

        # ── Bottom row: hint + help + buttons ──────────────────────────
        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        self._hint_label = QLabel(
            "Enter to insert  ·  Right / Left expands / collapses synonyms"
        )
        self._hint_label.setStyleSheet("color: #888; font-size: 8pt;")
        bottom.addWidget(self._hint_label, stretch=1)

        # v1.10.99 — contextual help button. Routed through the global
        # help_system so the URL resolution stays consistent with F1.
        self._help_btn = QPushButton("?")
        self._help_btn.setFixedSize(22, 22)
        self._help_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._help_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._help_btn.setToolTip("Open help for TermPicker (F1)")
        self._help_btn.setStyleSheet(
            """
            QPushButton {
                border: 1px solid #BDBDBD;
                border-radius: 11px;
                background: #FAFAFA;
                color: #555;
                font-size: 10pt;
                font-weight: bold;
                padding: 0;
            }
            QPushButton:hover {
                background: #E3F2FD;
                border-color: #1976D2;
                color: #1565C0;
            }
            """
        )
        self._help_btn.clicked.connect(self._open_help)
        bottom.addWidget(self._help_btn)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        ok_btn = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok_btn.setText("Insert")
        ok_btn.setAutoDefault(False)
        ok_btn.setDefault(False)
        ok_btn.clicked.connect(self._accept_current)
        self._buttons.button(QDialogButtonBox.StandardButton.Cancel).clicked.connect(self.reject)
        bottom.addWidget(self._buttons)
        outer.addLayout(bottom)

        # Tag the dialog itself so F1 anywhere inside it walks up to here.
        try:
            from modules.help_system import set_topic, Topics
            set_topic(self, Topics.GLOSSARY_TERM_PICKER)
        except Exception:
            pass

    def _open_help(self):
        """Open the TermPicker help page in the default browser."""
        try:
            from modules.help_system import open_help, Topics
            open_help(Topics.GLOSSARY_TERM_PICKER)
        except Exception:
            pass

    # ── Population ──────────────────────────────────────────────────────

    def _populate(self):
        """Build one top-level row per match. Synonyms are added as
        QTreeWidgetItem children, hidden by default — Right-arrow expands.
        """
        self._tree.clear()
        font = QFont("Segoe UI", 9)
        for match in self._matches:
            primary = match.get('primary') or {}
            synonyms = match.get('synonyms') or []
            index = match.get('index', 0)
            source_text = match.get('source_text', '') or ''
            target_text = primary.get('target_term', '') or ''
            termbase = primary.get('termbase_name', '') or ''
            is_project = bool(primary.get('is_project_termbase'))
            is_nt = bool(primary.get('is_nontranslatable'))

            has_expansion = len(synonyms) > 0
            # ▸ collapsed indicator on the # column — switches to ▾
            # when the user expands the row. Keeps the affordance
            # visible without taking a separate column.
            display_idx = str(index)
            if has_expansion:
                display_idx = f"{index} ▸"  # ▸

            item = QTreeWidgetItem([display_idx, source_text, target_text, termbase])
            # Right-align the # column (numbers read better that way).
            item.setTextAlignment(0, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
            item.setFont(0, font)
            item.setFont(1, font)
            item.setFont(2, font)
            item.setFont(3, font)
            # Store the insert text on the item for accept; the
            # parent-index lets us route 0-9 hotkeys.
            item.setData(0, Qt.ItemDataRole.UserRole, {
                'is_subitem': False,
                'parent_index': index,
                'target_term': target_text,
                'has_expansion': has_expansion,
            })
            # Row background.
            if is_nt:
                bg = _BG_NT
            elif is_project:
                bg = _BG_PROJECT
            else:
                bg = _BG_REGULAR
            for col in range(4):
                item.setBackground(col, QBrush(bg))

            # Build sub-items for synonyms but hide them initially via
            # setExpanded(False). QTreeWidget handles the show/hide.
            for syn in synonyms:
                sub_target = syn.get('target_term', '') or ''
                sub_tb = syn.get('termbase_name', '') or ''
                sub_source = '    └ ' + source_text  # └ prefix, indented
                sub = QTreeWidgetItem(['', sub_source, sub_target, sub_tb])
                sub.setFont(0, font)
                sub.setFont(1, font)
                sub.setFont(2, font)
                sub.setFont(3, font)
                for col in range(4):
                    sub.setBackground(col, QBrush(_BG_SUB))
                    sub.setForeground(col, QBrush(_FG_SUB))
                sub.setData(0, Qt.ItemDataRole.UserRole, {
                    'is_subitem': True,
                    'parent_index': index,
                    'target_term': sub_target,
                    'has_expansion': False,
                })
                item.addChild(sub)

            self._tree.addTopLevelItem(item)
            item.setExpanded(False)

        # Wire expand/collapse to swap the ▸/▾ glyph on the parent.
        self._tree.itemExpanded.connect(self._on_item_expanded)
        self._tree.itemCollapsed.connect(self._on_item_collapsed)

        # Select first row.
        if self._tree.topLevelItemCount() > 0:
            first = self._tree.topLevelItem(0)
            self._tree.setCurrentItem(first)

    def _on_item_expanded(self, item: QTreeWidgetItem):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, dict) and data.get('has_expansion'):
            idx = data.get('parent_index')
            item.setText(0, f"{idx} ▾")  # ▾

    def _on_item_collapsed(self, item: QTreeWidgetItem):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, dict) and data.get('has_expansion'):
            idx = data.get('parent_index')
            item.setText(0, f"{idx} ▸")  # ▸

    # ── Acceptance ──────────────────────────────────────────────────────

    def _accept_current(self):
        item = self._tree.currentItem()
        if item is None:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict):
            return
        target = data.get('target_term') or ''
        if not target:
            return
        self.selected_target_term = target
        self.accept()

    # ── Keyboard ────────────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        # 0-9 quick-pick: select the row whose # matches the digit and
        # auto-accept when total matches ≤ 9 (mirrors Trados; for ≥10
        # matches the user just sees the row select, then presses
        # Enter — avoids surprising auto-insert when the digit was an
        # off-by-one slip).
        if Qt.Key.Key_0 <= key <= Qt.Key.Key_9 and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            digit = key - Qt.Key.Key_0
            target = 10 if digit == 0 else digit
            self._select_by_index(target, auto_accept=(len(self._matches) <= 9))
            event.accept()
            return
        super().keyPressEvent(event)

    def _select_by_index(self, idx: int, auto_accept: bool):
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get('parent_index') == idx:
                self._tree.setCurrentItem(item)
                if auto_accept:
                    self._accept_current()
                return

    def eventFilter(self, obj, event):
        """Intercept Enter / Right / Left on the QTreeWidget so they
        drive accept and expand/collapse instead of QTreeWidget's
        default behaviours."""
        if obj is self._tree and event.type() == event.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._accept_current()
                return True
            if key == Qt.Key.Key_Right:
                item = self._tree.currentItem()
                if item is not None:
                    data = item.data(0, Qt.ItemDataRole.UserRole)
                    if isinstance(data, dict) and not data.get('is_subitem') and data.get('has_expansion'):
                        item.setExpanded(True)
                        return True
            if key == Qt.Key.Key_Left:
                item = self._tree.currentItem()
                if item is not None:
                    data = item.data(0, Qt.ItemDataRole.UserRole)
                    if isinstance(data, dict):
                        # Sub-item: jump to + collapse parent.
                        if data.get('is_subitem'):
                            parent = item.parent()
                            if parent is not None:
                                self._tree.setCurrentItem(parent)
                                parent.setExpanded(False)
                                return True
                        elif item.isExpanded():
                            item.setExpanded(False)
                            return True
        return super().eventFilter(obj, event)

    # ── Persistence ─────────────────────────────────────────────────────

    def closeEvent(self, event):
        if isinstance(self._settings, dict):
            self._settings['term_picker_width'] = self.width()
            self._settings['term_picker_height'] = self.height()
            self._settings['term_picker_column_widths'] = [
                self._tree.columnWidth(i) for i in range(self._tree.columnCount())
            ]
        super().closeEvent(event)


# ── Helper: convert raw termbase matches into the picker schema ────────


def build_picker_matches(
    termbase_matches: List[dict],
    nt_matches: Optional[List[dict]] = None,
) -> List[dict]:
    """Convert the raw match dicts produced by the segment search into
    the schema TermPickerDialog expects.

    Termbase matches are grouped by ``source_term`` so multiple entries
    on the same source word render as a single row with the first as
    primary and the rest as synonyms (mirrors how the docked TermLens
    chips collapse).

    NT matches each become their own row with the source = target text
    and is_nontranslatable=True so they get the amber background.
    """
    by_source: dict = {}
    order: list = []  # preserve first-seen order so the # column is stable
    for m in termbase_matches or []:
        source = (m.get('source_term') or m.get('source') or '').strip()
        target = (m.get('target_term') or m.get('translation') or '').strip()
        if not source or not target:
            continue
        key = source.lower()
        termbase_name = m.get('termbase_name', '') or ''
        # v1.10.88 — unpack each entry's target_synonyms into synonym
        # sub-rows so TermPicker's ▸/▾ expansion shows them too.
        # Pre-v1.10.88 the picker only treated "multiple termbase
        # entries that share the same source word" as synonyms; actual
        # ``target_synonyms`` on a single entry (the canonical "this
        # entry has alternate target spellings" case) collapsed into
        # the bare primary row with no way to see them, even though
        # the docked TermLens chip showed a ≡ corner indicator
        # promising they were there.
        target_synonyms = m.get('target_synonyms') or []
        if key not in by_source:
            by_source[key] = {
                'source_text': source,
                'primary': {
                    'target_term': target,
                    'termbase_name': termbase_name,
                    'is_project_termbase': bool(m.get('is_project_termbase', False)) or m.get('ranking') == 1,
                    'is_nontranslatable': bool(m.get('is_nontranslatable', False)),
                },
                'synonyms': [],
            }
            order.append(key)
            # Add this entry's own target_synonyms as sub-rows. Each
            # synonym shares the parent's termbase (synonyms are
            # per-entry, not per-termbase) so we tag them with that
            # name for the Termbase column.
            for syn in target_synonyms:
                if isinstance(syn, str) and syn.strip():
                    by_source[key]['synonyms'].append({
                        'target_term': syn.strip(),
                        'termbase_name': termbase_name,
                    })
        else:
            # Subsequent hits on the same source word become synonym
            # rows on the existing entry. The new entry's primary
            # target is one sub-row; its own target_synonyms become
            # additional sub-rows underneath the same parent.
            by_source[key]['synonyms'].append({
                'target_term': target,
                'termbase_name': termbase_name,
            })
            for syn in target_synonyms:
                if isinstance(syn, str) and syn.strip():
                    by_source[key]['synonyms'].append({
                        'target_term': syn.strip(),
                        'termbase_name': termbase_name,
                    })

    rows: List[dict] = []
    idx = 1
    for key in order:
        entry = by_source[key]
        entry['index'] = idx
        rows.append(entry)
        idx += 1

    for nt in nt_matches or []:
        text = nt.get('text', '') or ''
        if not text:
            continue
        rows.append({
            'index': idx,
            'source_text': text,
            'primary': {
                'target_term': text,
                'termbase_name': nt.get('list_name', 'Non-Translatables'),
                'is_project_termbase': False,
                'is_nontranslatable': True,
            },
            'synonyms': [],
        })
        idx += 1

    return rows
