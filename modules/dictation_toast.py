"""
dictation_toast.py – minimal frameless toast widget for dictation feedback.

Replaces the earlier QApplication.beep() approach which was unreliable
(many users have system sound muted) and harsh on Windows. The toast
sits at the top-right of the active screen, shows "🎤 Listening…", and
fades in on construction. The caller must call ``dismiss()`` when the
dictation finishes – there is intentionally no auto-timeout, because
push-to-talk recordings are arbitrarily long.

Usage from the main window::

    from modules.dictation_toast import show_dictation_toast
    self._dictation_toast = show_dictation_toast(self)
    # ... later, when recording ends:
    self._dictation_toast.dismiss()

The widget is owned by Qt; the caller only needs to keep a reference so
``dismiss()`` is reachable. Safe to call ``dismiss()`` more than once.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QPoint, QRectF
from PyQt6.QtGui import (
    QColor, QFont, QGuiApplication, QPainter, QBrush, QPen,
)
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel


class DictationToast(QWidget):
    """
    Frameless popup that floats top-right of the active screen and shows a
    single status line. Used to indicate that dictation has started after
    a push-to-talk hotkey was received.

    The toast paints its own dark rounded-rectangle background in
    paintEvent rather than relying on QSS + WA_TranslucentBackground +
    QGraphicsDropShadowEffect, because that stack rendered white-on-white
    in some compositing contexts (notably when the global hotkey fired
    while the user was in another app like Trados Studio).
    """

    _MARGIN_X = 24       # pixels from the right edge of the screen
    _MARGIN_Y = 56       # pixels from the top of the screen
    _MIN_WIDTH = 240
    _HEIGHT = 56
    _RADIUS = 10
    _FADE_MS = 180       # in/out fade duration
    _BG = QColor(36, 36, 36, 235)
    _FG = QColor(255, 255, 255)

    def __init__(self, parent_for_screen=None, text: str = "🎤  Listening…"):
        # No parent so the widget is independent of the main window's
        # focus/visibility – we want the toast to show even when the user
        # is in another app.
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        # Single label; layout just centres it. Background is painted in
        # paintEvent below so the rounded-corner panel is always opaque
        # regardless of compositing/styling quirks.
        self._label = QLabel(text, self)
        self._label.setStyleSheet(
            f"color: rgb({self._FG.red()}, {self._FG.green()}, {self._FG.blue()});"
            " background: transparent;"
        )
        font = QFont("Segoe UI", 11)
        font.setWeight(QFont.Weight.Medium)
        self._label.setFont(font)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 18, 10)
        layout.addWidget(self._label)

        # Size + position before showing
        self.resize(max(self._MIN_WIDTH, self._label.sizeHint().width() + 64), self._HEIGHT)
        self._reposition(parent_for_screen)

        # Fade-in
        self.setWindowOpacity(0.0)
        self._fade_in = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_in.setDuration(self._FADE_MS)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._dismissed = False

    # ---- Public API ----

    def show_with_fade(self):
        self.show()
        self.raise_()
        self._fade_in.start()

    def update_text(self, text: str):
        """Swap the displayed text in-place (e.g. 'Listening…' → 'Transcribing…')."""
        self._label.setText(text)

    def dismiss(self):
        """Fade out and close. Idempotent."""
        if self._dismissed:
            return
        self._dismissed = True
        try:
            self._fade_in.stop()
        except Exception:
            pass
        try:
            anim = QPropertyAnimation(self, b"windowOpacity", self)
            anim.setDuration(self._FADE_MS)
            anim.setStartValue(self.windowOpacity())
            anim.setEndValue(0.0)
            anim.setEasingCurve(QEasingCurve.Type.InCubic)
            anim.finished.connect(self.close)
            # Keep a ref so it isn't garbage-collected mid-animation
            self._fade_out = anim
            anim.start()
        except Exception:
            self.close()

    # ---- Internal ----

    def paintEvent(self, event):
        """Draw the dark rounded-rectangle background ourselves – relying
        on QSS + transparent-background + drop-shadow for this on Windows
        produced a transparent panel with white text invisible against
        whatever app was behind us."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(QBrush(self._BG))
        painter.setPen(QPen(Qt.PenStyle.NoPen))
        rect = QRectF(0, 0, self.width(), self.height())
        painter.drawRoundedRect(rect, self._RADIUS, self._RADIUS)

    def _reposition(self, parent_for_screen):
        """Place the toast at top-right of the screen the parent window is on,
        falling back to the primary screen."""
        screen = None
        try:
            if parent_for_screen is not None:
                handle = getattr(parent_for_screen, 'screen', None)
                if callable(handle):
                    screen = handle()
        except Exception:
            screen = None
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        x = avail.right() - self.width() - self._MARGIN_X
        y = avail.top() + self._MARGIN_Y
        self.move(QPoint(x, y))


def show_dictation_toast(parent_window=None, text: str = "🎤  Listening…") -> DictationToast:
    """Convenience entry point used by Supervertaler.py:start_voice_dictation."""
    toast = DictationToast(parent_for_screen=parent_window, text=text)
    toast.show_with_fade()
    return toast
