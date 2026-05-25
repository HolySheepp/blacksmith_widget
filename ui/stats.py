"""
Stats dialog — opened from the right-click context menu.
Shows play time, hit/force/click counters with show-on-anvil toggles,
and forge counts per metal type.  Auto-refreshes every second.
"""
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QCheckBox, QGroupBox, QWidget, QFrame, QPushButton,
)
from PyQt5.QtCore import Qt, QTimer
from game.metal import METAL_TYPES


def _fmt_time(seconds: float) -> str:
    s = int(seconds)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if h > 0:
        return f"{h}時{m:02d}分{s:02d}秒"
    if m > 0:
        return f"{m}分{s:02d}秒"
    return f"{s}秒"


class StatsDialog(QDialog):

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.setWindowTitle("📊  統計資料")
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.Dialog)
        self.setMinimumWidth(340)
        self._build_ui()
        self._load_from_state()

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(12, 12, 12, 12)

        sg = QGroupBox("遊戲統計")
        sf = QFormLayout()
        sf.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self.playtime_lbl  = QLabel()
        sf.addRow("遊玩時長:", self.playtime_lbl)

        self.hit_val_lbl   = QLabel()
        self.force_val_lbl = QLabel()
        self.click_val_lbl = QLabel()
        self.show_hit_cb   = QCheckBox()
        self.show_force_cb = QCheckBox()
        self.show_click_cb = QCheckBox()

        sf.addRow("打擊計數:", self._stat_row(self.hit_val_lbl,   self.show_hit_cb))
        sf.addRow("力道計數:", self._stat_row(self.force_val_lbl, self.show_force_cb))
        sf.addRow("點擊計數:", self._stat_row(self.click_val_lbl, self.show_click_cb))

        self.show_hit_cb.toggled.connect(
            lambda v: setattr(self.state, 'show_hit',   v))
        self.show_force_cb.toggled.connect(
            lambda v: setattr(self.state, 'show_force', v))
        self.show_click_cb.toggled.connect(
            lambda v: setattr(self.state, 'show_click', v))

        # Forge counts
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        sf.addRow(sep)

        self._forge_lbls = []
        for meta in METAL_TYPES:
            lbl = QLabel()
            sf.addRow(f"鍛造 {meta['name']}:", lbl)
            self._forge_lbls.append(lbl)

        sg.setLayout(sf)
        root.addWidget(sg)

        close_btn = QPushButton("關閉")
        close_btn.clicked.connect(self.accept)
        root.addWidget(close_btn)

    @staticmethod
    def _stat_row(val_lbl: QLabel, cb: QCheckBox) -> QWidget:
        w   = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(val_lbl, 1)
        row.addWidget(QLabel("  顯示在鐵砧"))
        row.addWidget(cb)
        return w

    # ── Load / refresh ────────────────────────────────────────────────────────

    def _load_from_state(self):
        s = self.state
        for cb, attr in [
            (self.show_hit_cb,   'show_hit'),
            (self.show_force_cb, 'show_force'),
            (self.show_click_cb, 'show_click'),
        ]:
            cb.blockSignals(True)
            cb.setChecked(getattr(s, attr))
            cb.blockSignals(False)
        self._refresh()

    def _refresh(self):
        s = self.state
        self.playtime_lbl.setText(_fmt_time(s.play_time))
        self.hit_val_lbl.setText(str(s.hit_count))
        self.force_val_lbl.setText(str(s.force_count))
        self.click_val_lbl.setText(str(s.click_count))
        fc = getattr(s, 'forge_counts', [])
        for i, lbl in enumerate(self._forge_lbls):
            lbl.setText(str(fc[i]) if i < len(fc) else "0")

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        self._timer.stop()
        super().closeEvent(event)
