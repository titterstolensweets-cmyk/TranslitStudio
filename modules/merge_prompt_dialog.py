"""
Merge Prompt Dialog ("Similar Term Found")

Shown when the user adds a term whose source OR target already exists in a
write termbase (same source but different target, or same target but different
source). Offers to fold the new term into the existing entry as a synonym
instead of creating a near-duplicate.

Port of the Trados plugin's MergePromptDialog so both products behave the same
against the shared SQLite termbase schema.

The chosen action is read from ``dialog.choice`` after ``exec()`` returns:

* ``MergePromptDialog.SYNONYM``   – Add as synonym (quick, no further dialog)
* ``MergePromptDialog.EDIT``      – Add as synonym then open the entry editor
* ``MergePromptDialog.KEEP_BOTH`` – Create a separate entry instead
* ``MergePromptDialog.CANCEL``    – Abort the add

Each match dict carries ``tb_was_swapped`` so the dialog can display the
existing entry in PROJECT direction even when its termbase stores rows in the
reverse direction.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame
)
from PyQt6.QtCore import Qt
from typing import List, Dict


class MergePromptDialog(QDialog):
    # Outcome constants (read via self.choice after exec()).
    CANCEL = "cancel"
    SYNONYM = "synonym"
    EDIT = "edit"
    KEEP_BOTH = "keep_both"

    def __init__(self, parent, matches: List[Dict],
                 new_source: str, new_target: str):
        """
        Args:
            matches: aggregated merge candidates. matches[0] is described in
                detail; any extras are summarised as "(and N more …)". Each
                dict has: term_id, source_term, target_term (existing entry, in
                its termbase's storage direction), match_type ('source'|'target',
                relative to that storage direction), termbase_name, and
                tb_was_swapped (True when the termbase is reverse of the project).
            new_source / new_target: the new term as typed by the translator, in
                PROJECT direction.
        """
        super().__init__(parent)
        self._matches = matches or []
        self._new_source = new_source or ""
        self._new_target = new_target or ""
        self.choice = self.CANCEL
        self._build_ui()

    def _build_ui(self):
        self.setWindowTitle("Similar Term Found")
        self.setModal(True)
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)

        # --- "You are adding:" ---
        layout.addWidget(QLabel("You are adding:"))
        new_term = QLabel(f"  {self._new_source}  →  {self._new_target}")
        new_term.setStyleSheet("font-weight: bold;")
        new_term.setWordWrap(True)
        layout.addWidget(new_term)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep1)

        # --- Match description (project direction) ---
        match = self._matches[0] if self._matches else {}
        swapped = match.get('tb_was_swapped', False)
        existing_source = match.get('source_term', '')
        existing_target = match.get('target_term', '')

        # Display the existing entry in PROJECT direction.
        display_match_source = existing_target if swapped else existing_source
        display_match_target = existing_source if swapped else existing_target

        # match_type is relative to termbase storage; flip when swapped.
        raw_type = match.get('match_type', 'source')
        if swapped:
            effective_type = 'target' if raw_type == 'source' else 'source'
        else:
            effective_type = raw_type

        if effective_type == 'source':
            description = (
                f"The source term “{display_match_source}” already "
                f"exists with target “{display_match_target}”"
            )
            action = (
                f"Add “{self._new_target}” as a target synonym "
                f"to the existing entry?"
            )
        else:
            description = (
                f"The target term “{display_match_target}” already "
                f"exists with source “{display_match_source}”"
            )
            action = (
                f"Add “{self._new_source}” as a source synonym "
                f"to the existing entry?"
            )

        description += (
            f"\nin termbase “{match.get('termbase_name', '')}”."
        )

        additional = len(self._matches) - 1
        if additional > 0:
            noun = "match" if additional == 1 else "matches"
            description += f"\n(and {additional} more {noun} in other termbases)"

        match_label = QLabel(description)
        match_label.setWordWrap(True)
        layout.addWidget(match_label)

        action_label = QLabel(action)
        action_label.setWordWrap(True)
        action_label.setStyleSheet("font-style: italic;")
        layout.addWidget(action_label)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep2)

        # --- Buttons (right-aligned) ---
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        btn_synonym = QPushButton("Add as Synonym")
        btn_synonym.setToolTip(
            "Quickly add the term as a synonym to the existing entry")
        btn_synonym.setDefault(True)
        btn_synonym.clicked.connect(lambda: self._finish(self.SYNONYM))

        btn_edit = QPushButton("Add && Edit…")
        btn_edit.setToolTip(
            "Add as synonym and open the term entry editor for review")
        btn_edit.clicked.connect(lambda: self._finish(self.EDIT))

        btn_keep = QPushButton("Keep Both")
        btn_keep.setToolTip(
            "Create a separate termbase entry instead of merging")
        btn_keep.clicked.connect(lambda: self._finish(self.KEEP_BOTH))

        btn_cancel = QPushButton("Cancel")
        btn_cancel.setToolTip("Cancel without adding the term")
        btn_cancel.clicked.connect(lambda: self._finish(self.CANCEL))

        for b in (btn_synonym, btn_edit, btn_keep, btn_cancel):
            btn_row.addWidget(b)
        layout.addLayout(btn_row)

    def _finish(self, choice: str):
        self.choice = choice
        if choice == self.CANCEL:
            self.reject()
        else:
            self.accept()

    def reject(self):
        # Esc / window close → treat as Cancel.
        self.choice = self.CANCEL
        super().reject()
