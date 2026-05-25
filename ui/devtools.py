"""
Dev Tools dialog — opened via the hidden passphrase in Settings.

Apply policy:
  • All sliders / text fields → staged; only written to state when an
    Apply button is clicked.

Buttons:
  [套用計數修改]    — write counter fields to state
  [套用蓄力設定]    — write charge-limit slider to state
  [套用渦輪設定]    — write fever slider + timing fields to state
  [全部套用]        — apply all of the above, stay open
  [套用並關閉]      — apply all, then close
  [關閉]            — close without applying pending changes
"""
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSlider, QGroupBox, QFormLayout, QFrame, QCheckBox,
)
from PyQt5.QtCore import Qt

_DEF_MAX_CHARGE = 5
_DEF_FEV_THRESH = 2
_DEF_FEV_DUR    = 20
_DEF_FEV_CD     = 75
_DEF_EX_LIFT    = 500   # default charge-EX lift (game units / s)
_DEF_WINDOW_MS  = 520   # default charge window duration (ms)


class DevToolsDialog(QDialog):

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.setWindowTitle("🔧 Dev Tools")
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.Dialog)
        self.setMinimumWidth(440)
        self._build_ui()
        self._load_from_state()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(12, 12, 12, 12)

        # ── 1. Counter editors ────────────────────────────────────────────────
        cg   = QGroupBox("計數修改")
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self.hit_edit   = QLineEdit()
        self.force_edit = QLineEdit()
        self.click_edit = QLineEdit()

        form.addRow("⚒  打擊次數:", self.hit_edit)
        form.addRow("◈  力道累積:", self.force_edit)
        form.addRow("✦  點擊次數:", self.click_edit)

        apply_ctr = QPushButton("套用計數修改")
        apply_ctr.clicked.connect(self._apply_counters)
        form.addRow(apply_ctr)
        cg.setLayout(form)
        root.addWidget(cg)

        # ── 2. 蓄力設定 ───────────────────────────────────────────────────────
        charge_box = QGroupBox("蓄力設定")
        cl = QVBoxLayout()

        slider_row = QHBoxLayout()
        slider_row.addWidget(QLabel(f"蓄力段數上限（默認 {_DEF_MAX_CHARGE}）:"))
        self.charge_slider = QSlider(Qt.Horizontal)
        self.charge_slider.setRange(1, 10)
        self.charge_slider.setTickInterval(1)
        self.charge_slider.setTickPosition(QSlider.TicksBelow)
        self.charge_lbl = QLabel()
        self.charge_lbl.setMinimumWidth(24)
        # valueChanged only updates the label — state not written until Apply
        self.charge_slider.valueChanged.connect(
            lambda v: self.charge_lbl.setText(str(v))
        )
        slider_row.addWidget(self.charge_slider)
        slider_row.addWidget(self.charge_lbl)
        cl.addLayout(slider_row)

        # 顯示蓄力條 toggle (applies immediately)
        cb_row = QHBoxLayout()
        cb_row.addWidget(QLabel("顯示蓄力條（默認關閉）:"))
        self.charge_bar_cb = QCheckBox()
        self.charge_bar_cb.toggled.connect(
            lambda v: setattr(self.state, 'show_charge_bar', v)
        )
        cb_row.addWidget(self.charge_bar_cb)
        cb_row.addStretch()
        cl.addLayout(cb_row)

        # 蓄力◆ 上抬強度 slider
        lift_row = QHBoxLayout()
        lift_row.addWidget(QLabel(f"蓄力◆上抬強度（默認 {_DEF_EX_LIFT}）:"))
        self.lift_slider = QSlider(Qt.Horizontal)
        self.lift_slider.setRange(5, 200)   # × 10  →  50 – 2000 units/s
        self.lift_slider.setTickInterval(1)
        self.lift_slider.setTickPosition(QSlider.TicksBelow)
        self.lift_lbl = QLabel()
        self.lift_lbl.setMinimumWidth(32)
        self.lift_slider.valueChanged.connect(
            lambda v: self.lift_lbl.setText(str(v * 10))
        )
        lift_row.addWidget(self.lift_slider)
        lift_row.addWidget(self.lift_lbl)
        cl.addLayout(lift_row)

        # 蓄力窗口時長
        window_row = QHBoxLayout()
        window_row.addWidget(QLabel(f"蓄力窗口（ms，默認 {_DEF_WINDOW_MS}）:"))
        self.window_edit = QLineEdit()
        self.window_edit.setToolTip("蓄力模式的計時窗口長度（毫秒），窗口結束時鐵錘自動下砸")
        window_row.addWidget(self.window_edit)
        cl.addLayout(window_row)

        apply_charge = QPushButton("套用蓄力設定")
        apply_charge.clicked.connect(self._apply_charge)
        cl.addWidget(apply_charge)
        charge_box.setLayout(cl)
        root.addWidget(charge_box)

        # ── 3. Turbo / Fever settings ─────────────────────────────────────────
        tg = QGroupBox("渦輪 / Fever 設定")
        tl = QVBoxLayout()
        tl.setSpacing(8)

        # Fever 滿蓄要求 slider
        fl = QHBoxLayout()
        fl.addWidget(QLabel(f"Fever 滿蓄要求（默認 {_DEF_FEV_THRESH}）:"))
        self.fever_thresh_slider = QSlider(Qt.Horizontal)
        self.fever_thresh_slider.setRange(1, 10)
        self.fever_thresh_slider.setTickInterval(1)
        self.fever_thresh_slider.setTickPosition(QSlider.TicksBelow)
        self.fever_thresh_lbl = QLabel()
        self.fever_thresh_lbl.setMinimumWidth(24)
        # valueChanged only updates label
        self.fever_thresh_slider.valueChanged.connect(
            lambda v: self.fever_thresh_lbl.setText(str(v))
        )
        fl.addWidget(self.fever_thresh_slider)
        fl.addWidget(self.fever_thresh_lbl)
        tl.addLayout(fl)

        timing_form = QFormLayout()
        self.fever_dur_edit = QLineEdit()
        self.fever_cd_edit  = QLineEdit()
        timing_form.addRow(f"Fever 持續秒數（默認 {_DEF_FEV_DUR}）:", self.fever_dur_edit)
        timing_form.addRow(f"Fever 冷卻秒數（默認 {_DEF_FEV_CD}）:",  self.fever_cd_edit)
        tl.addLayout(timing_form)

        apply_fever = QPushButton("套用渦輪設定")
        apply_fever.clicked.connect(self._apply_fever_settings)
        tl.addWidget(apply_fever)

        tg.setLayout(tl)
        root.addWidget(tg)

        # ── 4. 暴擊設定 ───────────────────────────────────────────────────────
        crit_box  = QGroupBox("暴擊設定")
        crit_form = QFormLayout()
        crit_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self.crit_rate_edit = QLineEdit()
        self.crit_rate_edit.setToolTip("暴擊率（%），例如 5.0 = 5%")
        self.crit_mult_edit = QLineEdit()
        self.crit_mult_edit.setToolTip("暴擊力道倍率，例如 3.0 = 3 倍")

        crit_form.addRow("暴擊率（%，默認 5.0）:", self.crit_rate_edit)
        crit_form.addRow("暴擊倍率（默認 3.0）:",   self.crit_mult_edit)

        apply_crit = QPushButton("套用暴擊設定")
        apply_crit.clicked.connect(self._apply_crit)
        crit_form.addRow(apply_crit)
        crit_box.setLayout(crit_form)
        root.addWidget(crit_box)

        # ── 5. Bottom buttons ─────────────────────────────────────────────────
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setFrameShadow(QFrame.Sunken)
        root.addWidget(sep2)

        btn_row = QHBoxLayout()
        apply_all_btn      = QPushButton("全部套用")
        apply_close_btn    = QPushButton("套用並關閉")
        close_btn          = QPushButton("關閉")

        apply_all_btn.clicked.connect(self._apply_all)
        apply_close_btn.clicked.connect(self._apply_all_and_close)
        close_btn.clicked.connect(self.reject)   # reject = close without side-effects

        btn_row.addWidget(apply_all_btn)
        btn_row.addWidget(apply_close_btn)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ── Load ──────────────────────────────────────────────────────────────────

    def _load_from_state(self):
        s = self.state
        self.hit_edit.setText(str(s.hit_count))
        self.force_edit.setText(str(s.force_count))
        self.click_edit.setText(str(s.click_count))

        self.charge_bar_cb.blockSignals(True)
        self.charge_bar_cb.setChecked(s.show_charge_bar)
        self.charge_bar_cb.blockSignals(False)

        self.charge_slider.setValue(s.typing_max_charge)
        self.charge_lbl.setText(str(s.typing_max_charge))

        lift_val = max(5, min(200, round(s.charge_ex_lift / 10)))
        self.lift_slider.blockSignals(True)
        self.lift_slider.setValue(lift_val)
        self.lift_slider.blockSignals(False)
        self.lift_lbl.setText(str(lift_val * 10))
        self.window_edit.setText(str(int(s.typing_base_ms)))
        self.fever_thresh_slider.setValue(s.fever_threshold)
        self.fever_thresh_lbl.setText(str(s.fever_threshold))
        self.fever_dur_edit.setText(str(int(s.fever_duration)))
        self.fever_cd_edit.setText(str(int(s.fever_cooldown_duration)))

        # Crit settings — rate stored as 0.0–1.0, displayed as %
        self.crit_rate_edit.setText(f"{s.crit_rate * 100:.1f}")
        self.crit_mult_edit.setText(f"{s.crit_mult:.1f}")

    # ── Individual apply actions ──────────────────────────────────────────────

    def _apply_counters(self):
        try:
            self.state.hit_count   = max(0, int(self.hit_edit.text()))
            self.state.force_count = max(0, int(self.force_edit.text()))
            self.state.click_count = max(0, int(self.click_edit.text()))
        except ValueError:
            pass

    def _apply_charge(self):
        self.state.typing_max_charge = self.charge_slider.value()
        self.state.charge_ex_lift    = self.lift_slider.value() * 10.0
        try:
            window = float(self.window_edit.text())
            self.state.typing_base_ms = max(50.0, min(5000.0, window))
        except ValueError:
            pass

    def _apply_fever_settings(self):
        self.state.fever_threshold = self.fever_thresh_slider.value()
        try:
            dur = float(self.fever_dur_edit.text())
            cd  = float(self.fever_cd_edit.text())
            self.state.fever_duration          = max(1.0, dur)
            self.state.fever_cooldown_duration = max(1.0, cd)
        except ValueError:
            pass

    def _apply_crit(self):
        try:
            rate = float(self.crit_rate_edit.text())
            self.state.crit_rate = max(0.0, min(1.0, rate / 100.0))
        except ValueError:
            pass
        try:
            mult = float(self.crit_mult_edit.text())
            self.state.crit_mult = max(1.0, mult)
        except ValueError:
            pass

    def _apply_all(self):
        self._apply_counters()
        self._apply_charge()
        self._apply_fever_settings()
        self._apply_crit()

    def _apply_all_and_close(self):
        self._apply_all()
        self.accept()
