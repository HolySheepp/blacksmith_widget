"""
ToastWidget — small non-intrusive notification bubble.
Auto-dismisses after 10 s; can also be closed with the × button.
No window decorations; always-on-top; transparent background.
"""
from PyQt5.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton
from PyQt5.QtCore    import Qt, QTimer
from PyQt5.QtGui     import QPainter, QColor, QBrush, QPen


class ToastWidget(QWidget):

    def __init__(self, title: str, body: str = ""):
        super().__init__(
            None,
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool,
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 10, 12)
        root.setSpacing(4)

        # ── Title row + close button ──────────────────────────────────────────
        top = QHBoxLayout()
        top.setSpacing(6)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(
            "color: #a0f0a0; font-weight: bold; font-size: 12px;"
        )
        top.addWidget(title_lbl, 1)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(18, 18)
        close_btn.setStyleSheet(
            "QPushButton { color: #888; border: none; font-size: 10px;"
            "              background: transparent; }"
            "QPushButton:hover { color: #eee; }"
        )
        close_btn.clicked.connect(self.close)
        top.addWidget(close_btn, 0, Qt.AlignTop)
        root.addLayout(top)

        # ── Body text ─────────────────────────────────────────────────────────
        if body:
            body_lbl = QLabel(body)
            body_lbl.setStyleSheet("color: #cccccc; font-size: 11px;")
            body_lbl.setWordWrap(True)
            root.addWidget(body_lbl)

        self.setFixedSize(260, 72)

        # Auto-dismiss after 10 s
        QTimer.singleShot(10_000, self.close)

    # ── Rounded dark background ───────────────────────────────────────────────

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QBrush(QColor(28, 32, 38, 230)))
        p.setPen(QPen(QColor(80, 180, 80, 160), 1.5))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 10, 10)
