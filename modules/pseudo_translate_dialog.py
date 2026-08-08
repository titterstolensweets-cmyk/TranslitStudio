"""Pseudo-translation — options dialog and apply/undo orchestration.

The pure, Qt-free transform lives in :mod:`modules.pseudo_translate` (and is
unit-tested there). This module is the UI/glue layer: it shows the options
dialog and writes the pseudo-translated targets into the project, recording a
single batched Undo so the operation is fully reversible.

``Supervertaler.py`` keeps only a thin ``pseudo_translate_bulk`` delegator that
calls :func:`run_pseudo_translation`, passing itself as ``main_window``. We take
the main window as a runtime argument (never importing it) so there is no
circular import.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
)

from modules.pseudo_translate import (
    MODE_ACCENTS,
    MODE_PLAIN,
    pseudo_translate_text,
)
from modules.styled_widgets import CheckmarkCheckBox, CheckmarkRadioButton


class PseudoTranslateDialog(QDialog):
    """Collect pseudo-translation options: scope, expansion, characters, markers."""

    def __init__(self, parent, total, selected_count=0, filtered_count=0):
        super().__init__(parent)
        self.setWindowTitle("Pseudo-translate (Export Test)")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Fill targets with deliberately stress-tested placeholder text, then "
            "export the document to check formatting, layout, encoding and tag "
            "round-trip <b>before</b> you start translating. Inline tags are "
            "preserved; the operation is reversible via Edit → Undo."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # ── Scope ──
        # Only offer radio choices when there's actually more than one scope.
        # With no selection and no filter there's just "all", so a lone radio
        # would look like an unchecked option you can't interact with — show a
        # plain label instead.
        scope_group = QGroupBox("Apply to")
        scope_layout = QVBoxLayout(scope_group)
        self._scope_buttons = QButtonGroup(self)
        self._rb_all = None
        self._rb_filtered = None
        self._rb_selected = None

        if not filtered_count and not selected_count:
            scope_layout.addWidget(QLabel(f"All {total} segments."))
        else:
            self._rb_all = CheckmarkRadioButton(f"All segments ({total})")
            scope_layout.addWidget(self._rb_all)
            self._scope_buttons.addButton(self._rb_all)

            if filtered_count:
                self._rb_filtered = CheckmarkRadioButton(
                    f"Filtered / visible segments ({filtered_count})"
                )
                scope_layout.addWidget(self._rb_filtered)
                self._scope_buttons.addButton(self._rb_filtered)

            if selected_count:
                self._rb_selected = CheckmarkRadioButton(
                    f"Selected segments ({selected_count})"
                )
                scope_layout.addWidget(self._rb_selected)
                self._scope_buttons.addButton(self._rb_selected)

            # Default: honour an explicit selection, otherwise the whole document.
            (self._rb_selected or self._rb_all).setChecked(True)
        layout.addWidget(scope_group)

        # ── Length expansion ──
        exp_group = QGroupBox("Length expansion")
        exp_layout = QVBoxLayout(exp_group)
        exp_hint = QLabel(
            "Simulates target-language growth so overflow, clipped cells and "
            "reflow show up. 0% = structure only, +30% typical, +100% max stress."
        )
        exp_hint.setWordWrap(True)
        exp_layout.addWidget(exp_hint)
        exp_row = QHBoxLayout()
        self._exp_spin = QSpinBox()
        self._exp_spin.setRange(0, 200)
        self._exp_spin.setSingleStep(10)
        self._exp_spin.setValue(30)
        self._exp_spin.setSuffix(" %")
        exp_row.addWidget(self._exp_spin)
        exp_row.addStretch(1)
        exp_layout.addLayout(exp_row)
        layout.addWidget(exp_group)

        # ── Character mode ──
        char_group = QGroupBox("Characters")
        char_layout = QVBoxLayout(char_group)
        self._mode_buttons = QButtonGroup(self)
        self._rb_accents = CheckmarkRadioButton("Accented characters (test diacritics, encoding, fonts)")
        self._rb_plain = CheckmarkRadioButton("Plain words (length + markers only)")
        self._rb_accents.setChecked(True)
        char_layout.addWidget(self._rb_accents)
        char_layout.addWidget(self._rb_plain)
        self._mode_buttons.addButton(self._rb_accents)
        self._mode_buttons.addButton(self._rb_plain)
        layout.addWidget(char_group)

        # ── Boundary markers ──
        self._markers = CheckmarkCheckBox(
            "Wrap each segment in ⟦ ⟧ boundary markers (spot dropped / merged segments)"
        )
        self._markers.setChecked(True)
        layout.addWidget(self._markers)

        # ── Buttons ──
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Pseudo-translate")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def options(self) -> dict:
        if self._rb_selected and self._rb_selected.isChecked():
            scope = "selected"
        elif self._rb_filtered and self._rb_filtered.isChecked():
            scope = "filtered"
        else:
            scope = "all"
        mode = MODE_ACCENTS if self._rb_accents.isChecked() else MODE_PLAIN
        return {
            "scope": scope,
            "expansion": self._exp_spin.value() / 100.0,
            "mode": mode,
            "markers": self._markers.isChecked(),
        }


def _gather_scopes(main_window):
    """Return (all_segments, selected_segments, filtered_segments_or_None)."""
    all_segs = list(main_window.current_project.segments)
    total = len(all_segs)

    try:
        selected = list(main_window.get_selected_segments_from_grid() or [])
    except Exception:
        selected = []

    visible = []
    try:
        table = main_window.table
        for row in range(table.rowCount()):
            if not table.isRowHidden(row) and row < total:
                visible.append(all_segs[row])
    except Exception:
        visible = []
    # Only treat it as a "filter" if it actually narrows the set.
    filtered = visible if 0 < len(visible) < total else None

    return all_segs, selected, filtered


def run_pseudo_translation(main_window):
    """Show the dialog and apply the pseudo-translation with batched Undo."""
    mw = main_window
    all_segs, selected, filtered = _gather_scopes(mw)
    total = len(all_segs)
    if total == 0:
        QMessageBox.information(mw, "Pseudo-translate", "There are no segments to pseudo-translate.")
        return

    dialog = PseudoTranslateDialog(
        mw,
        total=total,
        selected_count=len(selected),
        filtered_count=(len(filtered) if filtered else 0),
    )
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return

    opts = dialog.options()
    if opts["scope"] == "selected":
        targets = selected
    elif opts["scope"] == "filtered":
        targets = filtered or all_segs
    else:
        targets = all_segs

    if not targets:
        QMessageBox.information(mw, "Pseudo-translate", "No segments matched the chosen scope.")
        return

    # Confirm — call out any existing targets we'd overwrite.
    existing = sum(1 for s in targets if (s.target or "").strip())
    message = f"Fill {len(targets)} segment(s) with pseudo-translation for an export test?"
    if existing:
        message += (
            f"\n\nThis OVERWRITES {existing} existing target(s). "
            "Edit → Undo restores them."
        )
    message += "\n\nTip: run this on a copy of the project if you want to be cautious."
    reply = QMessageBox.warning(
        mw,
        "Pseudo-translate",
        message,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if reply != QMessageBox.StandardButton.Yes:
        return

    expansion = opts["expansion"]
    mode = opts["mode"]
    markers = opts["markers"]

    undo_entries = []
    changed = 0
    for segment in targets:
        new_target = pseudo_translate_text(
            segment.source, expansion=expansion, mode=mode, markers=markers
        )
        old_target = segment.target
        old_status = segment.status

        segment.target = new_target

        row = mw._find_row_for_segment(segment.id)
        if row is not None and row >= 0:
            widget = mw.table.cellWidget(row, 3)
            if widget:
                display = (
                    mw.apply_invisible_replacements(new_target)
                    if hasattr(mw, "apply_invisible_replacements")
                    else new_target
                )
                widget.blockSignals(True)
                widget.setPlainText(display)
                widget.blockSignals(False)

        if old_target != new_target:
            undo_entries.append((segment.id, old_target, new_target, old_status, old_status))
            changed += 1

    if undo_entries:
        try:
            mw.record_undo_states_batch(undo_entries)
        except Exception as exc:
            mw.log(f"⚠ Undo recording failed for pseudo-translate: {exc}")

    for method in ("auto_resize_rows", "update_progress_stats", "_mark_project_modified"):
        fn = getattr(mw, method, None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass

    mw.log(
        f"🧪 Pseudo-translated {changed} segment(s) "
        f"(expansion +{int(round(expansion * 100))}%, mode={mode}, "
        f"markers={'on' if markers else 'off'})"
    )
    QMessageBox.information(
        mw,
        "Pseudo-translation Complete",
        f"Filled {changed} segment(s) with pseudo-translation.\n\n"
        "Now export the document and check the layout. Use Edit → Undo to "
        "restore your targets.",
    )
