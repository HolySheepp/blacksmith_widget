"""
Settings dialog — opened from right-click context menu (⚙ 設定).

Statistics (read-only, auto-refreshes every second):
  遊玩時長 / 打擊計數 / 力道計數 / 點擊計數
  Three counters each have a "顯示在鐵砧" checkbox (applies immediately).

Settings:
  UI大小 slider  → staged, written on "套用UI大小"
  模式 radio     → applies immediately
  重置存檔       → confirmation dialog then full reset
"""
import sys
import os
import ctypes
import hashlib as _hlib
import winreg

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSlider, QGroupBox, QFormLayout, QFrame,
    QCheckBox, QRadioButton, QButtonGroup, QMessageBox, QWidget,
    QLineEdit,
)
from PyQt5.QtCore import Qt, QTimer

# Dev-tools gate — SHA-256 of the passphrase; plaintext NOT stored in source.
# The bytes [0x30,0x33,0x32,0x32] are the ASCII codes of the passphrase chars.
_DT_GATE = _hlib.sha256(bytes([0x30, 0x33, 0x32, 0x32])).hexdigest()

_REG_KEY  = r"Software\Microsoft\Windows\CurrentVersion\Run"
_REG_NAME = "BlacksmithWidget"


def _exe_path() -> str:
    """Return the path of the running executable (works for both .py and .exe)."""
    if getattr(sys, "frozen", False):
        return sys.executable          # PyInstaller bundle
    return os.path.abspath(sys.argv[0])  # running as .py script


def _startup_lnk_path() -> str:
    """Return full path of the Startup-folder shortcut."""
    startup = os.path.join(
        os.environ.get("APPDATA", ""),
        r"Microsoft\Windows\Start Menu\Programs\Startup",
    )
    return os.path.join(startup, "BlacksmithWidget.lnk")


def _autostart_get() -> bool:
    """Return True if the Startup-folder shortcut exists."""
    return os.path.exists(_startup_lnk_path())


def _create_lnk(target_exe: str, lnk_path: str) -> None:
    """Create a Windows .lnk shortcut via COM IShellLink (pure ctypes).
    No subprocess, no external deps — ole32.dll is always present on Windows."""

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_ulong),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    def _guid(d1, d2, d3, *d4):
        g = _GUID()
        g.Data1, g.Data2, g.Data3 = d1, d2, d3
        g.Data4 = (ctypes.c_ubyte * 8)(*d4)
        return g

    def _vf(ptr, idx, rtype, *atypes):
        """Call vtable method at index idx on a COM interface pointer."""
        vt = ctypes.cast(ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))[0],
                         ctypes.POINTER(ctypes.c_void_p))
        return ctypes.WINFUNCTYPE(rtype, ctypes.c_void_p, *atypes)(vt[idx])

    # CLSID_ShellLink  {00021401-0000-0000-C000-000000000046}
    # IID_IShellLinkW  {000214F9-0000-0000-C000-000000000046}
    # IID_IPersistFile {0000010B-0000-0000-C000-000000000046}
    CLSID_ShellLink  = _guid(0x00021401, 0, 0, 0xC0,0,0,0,0,0,0,0x46)
    IID_IShellLinkW  = _guid(0x000214F9, 0, 0, 0xC0,0,0,0,0,0,0,0x46)
    IID_IPersistFile = _guid(0x0000010B, 0, 0, 0xC0,0,0,0,0,0,0,0x46)

    ole32 = ctypes.WinDLL("ole32")

    # S_OK(0)=we initialised COM, S_FALSE(1)=already initialised same model — both mean we
    # own a reference and MUST call CoUninitialize.
    # Negative (e.g. RPC_E_CHANGED_MODE=0x80010106): already initialised with a different
    # apartment model — we did NOT add a reference, so we must NOT call CoUninitialize.
    coinit_hr  = ole32.CoInitialize(None)
    need_uninit = coinit_hr in (0, 1)

    psl = ctypes.c_void_p(None)
    try:
        hr = ole32.CoCreateInstance(
            ctypes.byref(CLSID_ShellLink), None, 1,   # 1 = CLSCTX_INPROC_SERVER
            ctypes.byref(IID_IShellLinkW), ctypes.byref(psl),
        )
        if hr != 0:
            return

        try:
            # IShellLinkW vtable (after 3 IUnknown slots):
            #   3=GetPath, 8=GetWorkingDirectory, 9=SetWorkingDirectory,
            #   19=Resolve, 20=SetPath
            _vf(psl, 20, ctypes.c_long, ctypes.c_wchar_p)(psl, target_exe)
            _vf(psl, 9,  ctypes.c_long, ctypes.c_wchar_p)(psl, os.path.dirname(target_exe))

            # QI for IPersistFile
            ppf = ctypes.c_void_p(None)
            hr = _vf(psl, 0, ctypes.c_long,
                     ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)
                     )(psl, ctypes.byref(IID_IPersistFile), ctypes.byref(ppf))
            if hr == 0 and ppf:
                try:
                    # IPersistFile vtable: [6]=Save
                    _vf(ppf, 6, ctypes.c_long,
                        ctypes.c_wchar_p, ctypes.c_int)(ppf, lnk_path, 1)
                finally:
                    try:
                        _vf(ppf, 2, ctypes.c_ulong)(ppf)   # Release IPersistFile
                    except Exception:
                        pass
        finally:
            try:
                _vf(psl, 2, ctypes.c_ulong)(psl)            # Release IShellLink
            except Exception:
                pass
    finally:
        if need_uninit:
            ole32.CoUninitialize()


def _autostart_set(enable: bool) -> None:
    """Create or remove the Startup-folder .lnk shortcut.
    Uses COM IShellLink via ctypes — no subprocess, no admin rights,
    no AV-suspicious behaviour."""
    lnk = _startup_lnk_path()
    if enable:
        try:
            _create_lnk(_exe_path(), lnk)
        except Exception:
            pass
    else:
        try:
            if os.path.exists(lnk):
                os.remove(lnk)
        except Exception:
            pass


def _autostart_migrate() -> None:
    """One-time migration: remove legacy registry Run key if it still exists.
    Called once at startup; after removal this function is a no-op forever."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY,
                            access=winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, _REG_NAME)
    except FileNotFoundError:
        pass
    except Exception:
        pass

_DEF_SCALE = 0.6


class SettingsDialog(QDialog):

    def __init__(self, state, parent=None, center_cb=None, devtools_cb=None,
                 always_on_top_cb=None):
        super().__init__(parent)
        self.state              = state
        self._center_cb         = center_cb
        self._devtools_cb       = devtools_cb
        self._always_on_top_cb  = always_on_top_cb
        self.setWindowTitle("⚙  設定")
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.Dialog)
        self.setMinimumWidth(420)
        self._build_ui()
        self._load_from_state()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(12, 12, 12, 12)

        # ── Settings ──────────────────────────────────────────────────────────
        cfg = QGroupBox("設定")
        cl  = QVBoxLayout()
        cl.setSpacing(8)

        # Hidden dev-tools access — passphrase input + unlabelled trigger button
        secret_row = QHBoxLayout()
        self._secret_input = QLineEdit()
        self._secret_input.setPlaceholderText("...")
        self._secret_input.setMaxLength(16)
        self._secret_input.returnPressed.connect(self._check_secret)
        self._secret_btn = QPushButton()   # no label — intentionally blank
        self._secret_btn.setFixedWidth(28)
        self._secret_btn.clicked.connect(self._check_secret)
        secret_row.addWidget(self._secret_input)
        secret_row.addWidget(self._secret_btn)
        cl.addLayout(secret_row)

        # UI scale
        scale_row = QHBoxLayout()
        scale_row.addWidget(QLabel(f"大小（默認 {int(_DEF_SCALE * 100)}%）:"))
        self.scale_slider = QSlider(Qt.Horizontal)
        self.scale_slider.setRange(3, 10)   # 0.3 → 1.0 in 0.1 steps
        self.scale_slider.setTickInterval(1)
        self.scale_slider.setTickPosition(QSlider.TicksBelow)
        self.scale_lbl = QLabel()
        self.scale_lbl.setMinimumWidth(44)
        # slider only updates label — state written on Apply
        self.scale_slider.valueChanged.connect(
            lambda v: self.scale_lbl.setText(f"{v * 10}%")
        )
        scale_row.addWidget(self.scale_slider)
        scale_row.addWidget(self.scale_lbl)
        cl.addLayout(scale_row)

        apply_scale_btn = QPushButton("套用大小")
        apply_scale_btn.clicked.connect(self._apply_scale)
        cl.addWidget(apply_scale_btn)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        cl.addWidget(sep)

        # Autostart
        auto_row = QHBoxLayout()
        auto_row.addWidget(QLabel("開機自動啟動:"))
        self.autostart_cb = QCheckBox()
        self.autostart_cb.setToolTip("將程式加入 Windows 開機啟動項")
        self.autostart_cb.toggled.connect(self._on_autostart_changed)
        auto_row.addWidget(self.autostart_cb)
        auto_row.addStretch()
        cl.addLayout(auto_row)

        # Always-on-top
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("永遠置頂:"))
        self.always_on_top_cb = QCheckBox()
        self.always_on_top_cb.setToolTip("關閉後，其他視窗可以覆蓋在鐵砧上方")
        self.always_on_top_cb.toggled.connect(self._on_always_on_top_changed)
        top_row.addWidget(self.always_on_top_cb)
        top_row.addStretch()
        cl.addLayout(top_row)

        sep1b = QFrame()
        sep1b.setFrameShape(QFrame.HLine)
        sep1b.setFrameShadow(QFrame.Sunken)
        cl.addWidget(sep1b)

        # Mode selection (applies immediately)
        cl.addWidget(QLabel("遊戲模式:"))
        self.charge_radio = QRadioButton("✪ 蓄力模式")
        self.combo_radio  = QRadioButton("❉ 連打模式")
        self.turbo_radio  = QRadioButton("⚡ 渦輪模式 (實驗)")
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.charge_radio, 0)
        self.mode_group.addButton(self.combo_radio,  1)
        self.mode_group.addButton(self.turbo_radio,  2)
        self.mode_group.buttonClicked.connect(self._on_mode_changed)

        mode_row1 = QHBoxLayout()
        mode_row1.addWidget(self.charge_radio)
        mode_row1.addWidget(self.combo_radio)
        mode_row1.addWidget(self.turbo_radio)
        mode_row1.addStretch()
        cl.addLayout(mode_row1)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setFrameShadow(QFrame.Sunken)
        cl.addWidget(sep2)

        # Art mode
        art_row = QHBoxLayout()
        art_row.addWidget(QLabel("美術相關功能:"))
        self.art_mode_cb = QCheckBox()
        self.art_mode_cb.setToolTip(
            "開啟後，在 PS / AI / AE / PR / Figma / Canva 等設計軟體中\n"
            "按住並拖曳滑鼠將視作連續點擊，讓美術工作者也能參與鍛造"
        )
        self.art_mode_cb.toggled.connect(
            lambda v: setattr(self.state, 'art_mode', v))
        art_row.addWidget(self.art_mode_cb)
        art_row.addStretch()
        cl.addLayout(art_row)

        # Center widget
        if self._center_cb is not None:
            center_btn = QPushButton("📌  移回螢幕中央")
            center_btn.clicked.connect(self._center_cb)
            cl.addWidget(center_btn)

        # Reset save
        reset_btn = QPushButton("🗑  重置存檔")
        reset_btn.setStyleSheet("color: #cc3333; font-weight: bold;")
        reset_btn.clicked.connect(self._confirm_reset)
        cl.addWidget(reset_btn)

        cfg.setLayout(cl)
        root.addWidget(cfg)

        # ── 視覺效果 ──────────────────────────────────────────────────────────
        vfx = QGroupBox("視覺效果")
        vl  = QVBoxLayout()
        vl.setSpacing(6)

        self.fx_hit_numbers_cb = QCheckBox("打擊數字跳出")
        self.fx_metal_forge_cb = QCheckBox("金屬鍛造")

        self.fx_hit_numbers_cb.setToolTip("打擊時在鐵砧上方顯示浮動數字")
        self.fx_metal_forge_cb.setToolTip("關閉後鐵砧上不會出現金屬塊，純粹打擊；已鍛造計數仍保留")

        self.fx_hit_numbers_cb.toggled.connect(
            lambda v: setattr(self.state, 'show_hit_numbers', v))
        self.fx_metal_forge_cb.toggled.connect(
            lambda v: setattr(self.state, 'show_metal_forge', v))

        row_fx1 = QHBoxLayout()
        row_fx1.addWidget(self.fx_hit_numbers_cb)
        row_fx1.addWidget(self.fx_metal_forge_cb)
        row_fx1.addStretch()
        vl.addLayout(row_fx1)

        vfx.setLayout(vl)
        root.addWidget(vfx)

        # ── Close ─────────────────────────────────────────────────────────────
        close_btn = QPushButton("關閉")
        close_btn.clicked.connect(self.accept)
        root.addWidget(close_btn)

    # ── Load / refresh ────────────────────────────────────────────────────────

    def _load_from_state(self):
        s = self.state

        # Block signals to avoid spurious setattr calls on load
        for cb, attr in [
            (self.fx_hit_numbers_cb, 'show_hit_numbers'),
            (self.fx_metal_forge_cb, 'show_metal_forge'),
        ]:
            cb.blockSignals(True)
            cb.setChecked(getattr(s, attr))
            cb.blockSignals(False)

        self.autostart_cb.blockSignals(True)
        self.autostart_cb.setChecked(self.state.autostart)
        self.autostart_cb.blockSignals(False)

        self.art_mode_cb.blockSignals(True)
        self.art_mode_cb.setChecked(self.state.art_mode)
        self.art_mode_cb.blockSignals(False)

        self.always_on_top_cb.blockSignals(True)
        self.always_on_top_cb.setChecked(self.state.always_on_top)
        self.always_on_top_cb.blockSignals(False)

        scale_int = max(3, min(10, round(s.ui_scale * 10)))
        self.scale_slider.blockSignals(True)
        self.scale_slider.setValue(scale_int)
        self.scale_slider.blockSignals(False)
        self.scale_lbl.setText(f"{scale_int * 10}%")

        in_fever = s.turbo_mode and s.fever_active
        if s.turbo_mode:
            self.turbo_radio.setChecked(True)
        elif s.kb_mode == "combo":
            self.combo_radio.setChecked(True)
        else:
            self.charge_radio.setChecked(True)
        for rb in (self.charge_radio, self.combo_radio, self.turbo_radio):
            rb.setEnabled(not in_fever)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_autostart_changed(self, enabled: bool):
        self.state.autostart = enabled
        _autostart_set(enabled)

    def _on_always_on_top_changed(self, enabled: bool):
        self.state.always_on_top = enabled
        if self._always_on_top_cb is not None:
            self._always_on_top_cb(enabled)

    def _apply_scale(self):
        self.state.ui_scale = self.scale_slider.value() / 10.0

    def _on_mode_changed(self, button):
        s = self.state
        if s.turbo_mode and s.fever_active:
            return   # cannot switch during fever

        if button is self.charge_radio:
            new_mode, new_turbo = "charge", False
        elif button is self.combo_radio:
            new_mode, new_turbo = "combo",  False
        else:  # turbo_radio
            new_mode, new_turbo = "charge", True

        # Apply turbo toggle if it changed
        if new_turbo != s.turbo_mode:
            s.turbo_mode = new_turbo
            if not s.turbo_mode:
                if s.fever_active:
                    s._exit_fever()
                s.fever_cooldown_timer    = 0.0
                s.consecutive_full_charge = 0

        if new_mode == s.kb_mode and not (button is self.turbo_radio and not s.turbo_mode):
            return   # nothing changed

        s.kb_mode             = new_mode
        s.kb_state            = "idle"
        s.kb_active           = False
        s.space_queue         = 0
        s.typing_wants_strike = False
        s.typing_charge       = 0
        s.typing_cooldown     = 0.0
        s.charge_pulses.clear()
        s.charge_ex_armed     = False
        s.charge_ex_timer     = 0.0
        s.charge_prefire      = False

    def _check_secret(self):
        """Verify passphrase and open Dev Tools if it matches."""
        code = self._secret_input.text()
        if _hlib.sha256(code.encode()).hexdigest() == _DT_GATE:
            self._secret_input.clear()
            if self._devtools_cb is not None:
                self._devtools_cb()
        else:
            self._secret_input.clear()   # wrong code — clear silently, give no hint

    def _confirm_reset(self):
        reply = QMessageBox.question(
            self,
            "確認重置",
            "這將清空所有統計資料，並將全部設定恢復為默認值。\n\n確定要重置嗎？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.state.reset_save()
            self._load_from_state()

