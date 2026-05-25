"""
BlacksmithWidget — transparent, always-on-top desktop widget.
- Background fully transparent; only anvil, hammer, sparks, HUD drawn.
- Mouse does NOT control the hammer (keyboard-only).
- Left-drag → move widget.  Right-click → context menu.
- Auto-saves every 60 s and on every clean exit.
- Secret: 5 consecutive left-clicks (each ≤1 s apart) inside the
  horn-tip triangle → Dev Tools dialog.
"""
import os
import pathlib
import subprocess
import sys
import threading
import time

from PyQt5.QtWidgets import (QWidget, QMenu, QAction, QDialog, QVBoxLayout,
                              QLabel, QMessageBox, QProgressBar, QSystemTrayIcon)
from PyQt5.QtCore    import Qt, QTimer, QPoint, pyqtSlot, pyqtSignal
from PyQt5.QtGui     import QPainter, QIcon, QPixmap

from config         import WIDGET_W, WIDGET_H
from game.state     import GameState
from ui.renderer    import draw_frame
from ui.devtools    import DevToolsDialog
from ui.settings    import SettingsDialog
from ui.stats       import StatsDialog
from input.listener import KeyboardListener
from save           import write_save
from ui.settings    import _autostart_set

def _wlog(msg: str) -> None:
    """Append one line to the startup log from within the widget (best-effort)."""
    try:
        p = (pathlib.Path(os.environ["APPDATA"])
             / "BlacksmithWidget" / "startup.log")
        with open(p, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


_FRAME_MS      = 16
_AUTOSAVE_MS   = 60_000
_DRAG_THRESH2  = 25
_UPDATE_MS     = 3_600_000   # hourly update check interval (ms)

# Dev Tools are now accessed via the hidden passphrase input in Settings dialog.


class BlacksmithWidget(QWidget):

    # Signals used to marshal results from background threads → main thread
    _update_ready = pyqtSignal(str, str, bool)  # (tag, download_url, show_toast)
    _dl_progress  = pyqtSignal(int)             # download progress 0-100
    _dl_done      = pyqtSignal(bool)            # download finished (success?)
    _check_msg    = pyqtSignal(str, str)        # (title, body) info message

    def __init__(self):
        _wlog("[init] super().__init__()")
        super().__init__()

        _wlog("[init] setWindowFlags / setAttribute")
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFixedSize(WIDGET_W, WIDGET_H)

        _wlog("[init] GameState()")
        self.state = GameState()
        _wlog("[init] autostart_set")
        _autostart_set(self.state.autostart)   # apply saved autostart preference on every launch
        # Apply saved always-on-top preference before first show() — no visible flicker
        if not self.state.always_on_top:
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool)

        # Restore last window position (saved in logical pixels)
        if self.state.widget_x is not None and self.state.widget_y is not None:
            self.move(self.state.widget_x, self.state.widget_y)

        _wlog("[init] KeyboardListener()")
        self.listener = KeyboardListener()
        self.listener.key_pressed.connect(self._on_key)
        # Root cause of "process exists but no window" on --onefile builds:
        #   PyInstaller --onefile runs Python in a child process.  On Windows,
        #   the very FIRST Python-level thread creation (threading.Thread.start)
        #   in that child process can deadlock before Python's threading
        #   subsystem is fully initialised.  pynput's Listener.start() happens
        #   to be that first call, so the app freezes before show() is reached.
        #
        # Fix: defer listener startup until AFTER app.exec_() has started.
        #   Qt initialises its own native threads during event-loop startup,
        #   which bootstraps Python's threading state and breaks the deadlock.
        #   QTimer.singleShot(0) fires on the very first event-loop iteration —
        #   safely after Qt threads are up, but before any user interaction.
        #   Inside the callback we still spin a daemon thread so that
        #   pynput's internal _ready.wait() never blocks the main thread.
        # ── Force Windows message queue creation before any SetTimer() calls ────
        # PyInstaller --onefile extracts Qt DLLs to %TEMP%\_MEI...\  On some
        # machines, Qt's event dispatcher hangs creating its internal
        # message-only window (CreateWindowEx/SetTimer) because security
        # software is scanning DLLs in the TEMP path.
        # Calling PeekMessageW() forces Windows to create the thread's message
        # queue immediately; subsequent SetTimer() calls post WM_TIMER to that
        # existing queue and never need CreateWindowEx again.
        _wlog("[init] PeekMessageW (force message queue)")
        try:
            import ctypes
            import ctypes.wintypes as _wt
            _msg = _wt.MSG()
            ctypes.windll.user32.PeekMessageW(
                ctypes.byref(_msg), None, 0, 0, 0)   # PM_NOREMOVE = 0
            _wlog("[init] PeekMessageW OK")
        except Exception as _e:
            _wlog(f"[init] PeekMessageW failed (non-fatal): {_e}")

        # ── Timers — created here but NOT started yet ─────────────────────────
        # Root cause of the hang at _timer.start():
        #   QTimer.start() calls QEventDispatcherWin32::registerTimer(), which on
        #   its first call invokes createInternalHwnd() → CreateWindowExW(HWND_MESSAGE).
        #   On machines with security software scanning %TEMP%\_MEI..., that
        #   CreateWindowEx call blocks for tens of seconds.
        # Fix: defer ALL SetTimer()-based starts to _start_timers(), which fires
        #   via singleShot(0) on the FIRST event-loop iteration — AFTER exec()
        #   has already called processEvents() → createInternalHwnd() itself.
        #   By then the internal HWND exists; SetTimer() just posts a WM_TIMER to
        #   the existing queue and returns instantly.
        _wlog("[init] frame/save timers (configured, start deferred)")
        self._timer = QTimer(self)
        self._timer.setInterval(_FRAME_MS)
        self._timer.timeout.connect(self._tick)

        self._save_timer = QTimer(self)
        self._save_timer.setInterval(_AUTOSAVE_MS)
        self._save_timer.timeout.connect(self._autosave)

        # Prepare update timer (frozen exe only) — NOT started yet
        if getattr(sys, "frozen", False):
            self._update_timer = QTimer(self)
            self._update_timer.setInterval(_UPDATE_MS)
            self._update_timer.timeout.connect(self._start_update_check)

        # singleShot(0) uses QMetaObject::invokeMethod(QueuedConnection) — NOT
        # SetTimer — so it fires on the very first event-loop iteration without
        # any Win32 timer machinery.  _start_timers() then calls .start() on all
        # timers safely, after createInternalHwnd() is guaranteed to exist.
        self.setMouseTracking(True)   # needed for hover detection (enterEvent alone isn't enough during drags)

        _wlog("[init] QTimer.singleShot(0) — all starts deferred to _start_timers()")
        QTimer.singleShot(0, self._start_timers)

        # Drag state
        self._press_global: QPoint | None = None
        self._drag_offset:  QPoint | None = None
        self._is_dragging:  bool          = False

        # Ghost guide hide delay — leaveEvent starts this; enterEvent cancels it.
        # Fires after 1 s to clear mouse_on_widget so the guide fades out.
        self._ghost_hide_timer = QTimer(self)
        self._ghost_hide_timer.setSingleShot(True)
        self._ghost_hide_timer.setInterval(400)
        self._ghost_hide_timer.timeout.connect(
            lambda: setattr(self.state, "mouse_on_widget", False)
        )

        # Dev Tools dialog (opened via Settings passphrase)
        self._dt_dialog: DevToolsDialog | None = None

        # Settings dialog
        self._settings_dialog: SettingsDialog | None = None

        # Stats dialog
        self._stats_dialog: StatsDialog | None = None

        # System tray icon
        self._tray: QSystemTrayIcon | None = None

        # Auto-update state
        self._update_dlg:     QDialog | None  = None
        self._update_new_exe: str             = ""
        self._update_old_exe: str             = ""
        self._pending_update: dict | None     = None
        self._update_toast:   object          = None
        self._update_dlg_lbl: object          = None
        self._countdown_timer: QTimer | None  = None
        self._countdown_n:    int             = 0
        _wlog("[init] instance vars done")
        _wlog("[init] signal connections")
        self._update_ready.connect(self._on_update_ready)
        self._dl_done.connect(self._on_dl_done)
        self._check_msg.connect(lambda t, b: QMessageBox.information(self, t, b))

        # ── Pre-create native HWND ─────────────────────────────────────────────
        # Qt uses lazy HWND creation: the native window is created on the first
        # call to show() / winId() / raise_().  On machines where security
        # software intercepts CreateWindowExW via a CBT hook (e.g. scanning
        # DLLs in %TEMP%\_MEI...), this can block for 10–30 s during show().
        # Calling winId() HERE forces HWND creation immediately, INSIDE __init__,
        # while the event loop is not yet running.  The CBT hook still fires, but
        # now it fires during widget construction (expected), not during show(),
        # and subsequent show() calls just map the existing HWND — no second
        # CreateWindowExW — so show() returns instantly.
        _wlog("[init] winId() — pre-creating native HWND")
        try:
            _hwnd = int(self.winId())
            _wlog(f"[init] HWND pre-created: {_hwnd:#010x}")
        except Exception as _e:
            _wlog(f"[init] winId() failed (non-fatal): {_e}")

        _wlog("[init] tray icon")
        self._setup_tray()
        _wlog("[init] __init__ complete")

    # ── Input listener ────────────────────────────────────────────────────────

    def _start_timers(self):
        """Called via singleShot(0) — fires on the first event-loop iteration.
        By this point QEventDispatcherWin32.processEvents() has already called
        createInternalHwnd(), so all subsequent SetTimer() calls post to an
        existing HWND and return instantly without ever blocking."""
        _wlog("[timers] _start_timers — createInternalHwnd guaranteed, starting timers")
        self._timer.start()
        _wlog("[timers] _timer started")
        self._save_timer.start()
        _wlog("[timers] _save_timer started")
        # Listener: spin up in a daemon thread so pynput's _ready.wait() never
        # blocks the main thread.
        threading.Thread(target=self._start_listener_safe, daemon=True).start()
        _wlog("[timers] listener thread dispatched")
        # Frozen-exe only: schedule an update check 2 s after startup, plus
        # a periodic hourly check.
        if getattr(sys, "frozen", False):
            QTimer.singleShot(2000, lambda: self._start_update_check(True))
            _wlog("[timers] update check scheduled (2 s)")
            if hasattr(self, "_update_timer"):
                self._update_timer.start()
                _wlog("[timers] _update_timer started")
        _wlog("[timers] _start_timers complete")

    def _start_listener_safe(self):
        """Start pynput listener in a background thread.
        If the hook installation blocks or fails (AV software, security policy),
        this silently gives up so the window is always visible."""
        try:
            self.listener.start()
            _wlog("[listener] listener started OK")
        except Exception as exc:
            _wlog(f"[listener] listener FAILED: {exc}")

    # ── Screen helpers ────────────────────────────────────────────────────────

    def _ensure_on_screen(self):
        """If the widget is entirely off every screen, snap to primary screen centre."""
        from PyQt5.QtWidgets import QApplication
        desktop = QApplication.desktop()
        frame   = self.frameGeometry()
        on_any  = any(
            desktop.screenGeometry(i).intersects(frame)
            for i in range(desktop.screenCount())
        )
        if not on_any:
            geo = desktop.availableGeometry()          # primary screen
            self.move(geo.center() - self.rect().center())

    def _move_to_center(self):
        """Move widget to the centre of whichever screen it currently overlaps."""
        from PyQt5.QtWidgets import QApplication
        geo = QApplication.desktop().availableGeometry(self)
        self.move(geo.center() - self.rect().center())

    # ── Game loop ─────────────────────────────────────────────────────────────

    def _tick(self):
        self.state.update(_FRAME_MS)
        # Auto-resize when ui_scale changes
        new_w = int(800 * self.state.ui_scale)
        new_h = int(600 * self.state.ui_scale)
        if self.width() != new_w or self.height() != new_h:
            self.setFixedSize(new_w, new_h)
        self.update()

    def _autosave(self):
        write_save(self.state.to_save())

    def showEvent(self, event):
        super().showEvent(event)
        self._ensure_on_screen()

    # ── Paint ─────────────────────────────────────────────────────────────────

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        draw_frame(painter, self.state)
        painter.end()

    # ── Keyboard ──────────────────────────────────────────────────────────────

    @pyqtSlot(str)
    def _on_key(self, key: str):
        self.state.on_key_event(key)

    # ── Mouse ─────────────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if not self.state.lock_position:
                self._press_global = event.globalPos()
                self._drag_offset  = event.globalPos() - self.frameGeometry().topLeft()
                self._is_dragging  = False
        elif event.button() == Qt.RightButton:
            self._show_context_menu(event.globalPos())

    def mouseMoveEvent(self, event):
        # Always update hover state (mouse tracking enabled)
        self.state.mouse_on_widget = True
        # Drag
        if not (event.buttons() & Qt.LeftButton) or self._press_global is None:
            return
        delta = event.globalPos() - self._press_global
        if not self._is_dragging:
            if delta.x() ** 2 + delta.y() ** 2 > _DRAG_THRESH2:
                self._is_dragging = True
        if self._is_dragging and self._drag_offset is not None:
            self.move(event.globalPos() - self._drag_offset)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._is_dragging  = False
            self._press_global = None
            self._drag_offset  = None

    def enterEvent(self, event):
        self._ghost_hide_timer.stop()   # cancel any pending fade-out
        self.state.mouse_on_widget = True
        super().enterEvent(event)

    def leaveEvent(self, event):
        # Don't hide immediately — give the player 1 s to reach the ghost circle.
        self._ghost_hide_timer.start()
        super().leaveEvent(event)

    def _place_dialog(self, dlg):
        """Show dlg next to the widget, guaranteed fully on screen.
        Prefers the right side; falls back to the left; clamps vertically."""
        from PyQt5.QtWidgets import QApplication
        dlg.adjustSize()
        screen = QApplication.desktop().availableGeometry(self)
        geo    = self.frameGeometry()
        dw, dh = dlg.width(), dlg.height()

        x = geo.right() + 8                          # try right
        if x + dw > screen.right():
            x = geo.left() - dw - 8                  # fall back to left
        x = max(screen.left(), min(x, screen.right() - dw))  # clamp

        y = geo.top()
        y = max(screen.top(), min(y, screen.bottom() - dh))  # clamp

        dlg.move(x, y)
        dlg.show()

    def _open_devtools(self):
        if self._dt_dialog is not None and self._dt_dialog.isVisible():
            self._dt_dialog.raise_()
            self._dt_dialog.activateWindow()
            return
        dlg = DevToolsDialog(self.state, self)
        self._dt_dialog = dlg
        self._place_dialog(dlg)

    def _open_stats(self):
        if self._stats_dialog is not None and self._stats_dialog.isVisible():
            self._stats_dialog.raise_()
            self._stats_dialog.activateWindow()
            return
        dlg = StatsDialog(self.state, self)
        self._stats_dialog = dlg
        self._place_dialog(dlg)

    def _apply_always_on_top(self, enabled: bool):
        """Apply the always-on-top window flag and re-show.  Called by settings checkbox
        and by _toggle_always_on_top.  Does NOT update state — caller is responsible."""
        if enabled:
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        else:
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool)
        self.show()   # required after setWindowFlags to make the change take effect

    def _open_settings(self):
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return
        dlg = SettingsDialog(self.state, self,
                             center_cb=self._move_to_center,
                             devtools_cb=self._open_devtools,
                             always_on_top_cb=self._apply_always_on_top)
        self._settings_dialog = dlg
        self._place_dialog(dlg)

    # ── Context menu ──────────────────────────────────────────────────────────

    def _show_context_menu(self, global_pos: QPoint):
        menu = QMenu(self)
        s = self.state

        in_fever = s.turbo_mode and s.fever_active

        # ── Charge / combo toggle (hidden while turbo is active) ──────────
        if not s.turbo_mode:
            if s.kb_mode == "charge_legacy":
                toggle = QAction("🔨  蓄力(舊版)模式", self)
                toggle.setEnabled(False)
            elif s.kb_mode == "charge":
                toggle = QAction("⚡  切換為連打模式", self)
                toggle.triggered.connect(self._toggle_mode)
            else:
                toggle = QAction("🔥  切換為蓄力模式", self)
                toggle.triggered.connect(self._toggle_mode)
            menu.addAction(toggle)

        # ── Turbo toggle ──────────────────────────────────────────────────
        if in_fever:
            turbo_act = QAction("🔥  Fever 進行中（無法切換）", self)
            turbo_act.setEnabled(False)
        elif s.turbo_mode:
            turbo_act = QAction("⚡  關閉渦輪模式", self)
            turbo_act.triggered.connect(self._toggle_turbo)
        else:
            turbo_act = QAction("⚡  開啟渦輪模式 (實驗性)", self)
            turbo_act.triggered.connect(self._toggle_turbo)
        menu.addAction(turbo_act)

        menu.addSeparator()

        # ── Hide anvil toggle ─────────────────────────────────────────────
        hide_act = QAction("👁  顯示鐵砧" if s.hide_anvil else "🫥  隱藏鐵砧", self)
        hide_act.triggered.connect(self._toggle_hide_anvil)
        menu.addAction(hide_act)

        # ── Lock position toggle ──────────────────────────────────────────
        lock_act = QAction("🔓  解除鎖定位置" if s.lock_position else "🔒  鎖定位置", self)
        lock_act.triggered.connect(self._toggle_lock_position)
        menu.addAction(lock_act)

        # ── Always-on-top toggle ──────────────────────────────────────────
        top_act = QAction("🔝  取消永遠置頂" if s.always_on_top else "🔝  永遠置頂", self)
        top_act.triggered.connect(self._toggle_always_on_top)
        menu.addAction(top_act)

        menu.addSeparator()

        stats_act = QAction("📊  統計資料", self)
        stats_act.triggered.connect(self._open_stats)
        menu.addAction(stats_act)

        settings_act = QAction("⚙  設定", self)
        settings_act.triggered.connect(self._open_settings)
        menu.addAction(settings_act)

        menu.addSeparator()

        if self._pending_update:
            tag        = self._pending_update["tag"]
            update_act = QAction(f"🆕  檢測到新版本 {tag}  點我更新！", self)
            update_act.triggered.connect(self._prompt_and_update)
        else:
            update_act = QAction("🔍  檢查更新", self)
            update_act.triggered.connect(self._check_update_manual)
        menu.addAction(update_act)

        restart_act = QAction("🔄  重啟", self)
        restart_act.triggered.connect(self._restart)
        menu.addAction(restart_act)

        quit_act = QAction("✕  退出", self)
        quit_act.triggered.connect(self.close)
        menu.addAction(quit_act)

        menu.exec_(global_pos)

    def _toggle_mode(self):
        s = self.state
        if s.turbo_mode or s.kb_mode == "charge_legacy":
            return   # these modes are managed from Settings
        if s.turbo_mode and s.fever_active:
            return
        s.kb_mode             = "combo" if s.kb_mode == "charge" else "charge"
        s.kb_state            = "idle"
        s.kb_active           = False
        s.space_queue         = 0
        s.typing_pending      = False
        s.typing_wants_strike = False
        s.typing_charge       = 0
        s.typing_cooldown     = 0.0
        s.charge_pulses.clear()
        s.charge_ex_armed     = False
        s.charge_ex_timer     = 0.0

    def _toggle_turbo(self):
        s = self.state
        if s.turbo_mode and s.fever_active:
            return   # cannot switch during fever
        s.turbo_mode = not s.turbo_mode
        if not s.turbo_mode:
            if s.fever_active:
                s._exit_fever()
            s.fever_cooldown_timer    = 0.0
            s.consecutive_full_charge = 0
        else:
            # Turbo just enabled — combo is incompatible; fall back to charge
            if s.kb_mode == "combo":
                s.kb_mode = "charge"
        s.kb_state            = "idle"
        s.kb_active           = False
        s.space_queue         = 0
        s.typing_pending      = False
        s.typing_wants_strike = False
        s.typing_charge       = 0
        s.typing_cooldown     = 0.0
        s.charge_pulses.clear()
        s.charge_ex_armed     = False
        s.charge_ex_timer     = 0.0

    def _toggle_hide_anvil(self):
        self.state.hide_anvil = not self.state.hide_anvil

    def _toggle_lock_position(self):
        self.state.lock_position = not self.state.lock_position

    def _toggle_always_on_top(self):
        self.state.always_on_top = not self.state.always_on_top
        self._apply_always_on_top(self.state.always_on_top)

    # ── Auto-update ───────────────────────────────────────────────────────────

    def _start_update_check(self, is_startup: bool = False):
        """Kick off a background version check."""
        threading.Thread(
            target=lambda: self._check_update_bg(is_startup),
            daemon=True,
        ).start()

    def _check_update_bg(self, is_startup: bool = False):
        """Background thread: fetch latest release info silently."""
        import update as upd
        from config import VERSION
        info = upd.fetch_latest()
        if info and upd.is_newer(info["tag"], VERSION):
            self._update_ready.emit(info["tag"], info["url"], is_startup)

    def _on_update_ready(self, tag: str, url: str, show_toast: bool):
        """Main thread: store pending update; optionally show toast bubble."""
        self._pending_update = {"tag": tag, "url": url}
        if show_toast:
            self._show_update_toast(tag)

    def _show_update_toast(self, tag: str):
        """Show a small notification bubble just above the hammer / anvil area.
        Falls back to bottom-right corner if there is not enough room above."""
        from ui.toast import ToastWidget
        from PyQt5.QtWidgets import QApplication
        from config import KB_X, KB_Y, HEAD_PERP, SCALE

        toast = ToastWidget(f"🆕 發現新版本 {tag}", "右鍵選單即可更新。")
        tw, th = toast.width(), toast.height()

        screen = QApplication.desktop().availableGeometry(self)
        wp = self.pos()                       # widget top-left in screen coords

        # Hammer centre in widget pixels
        hx_w = int(KB_X   * SCALE)           # ≈ 271
        hy_w = int((KB_Y - HEAD_PERP) * SCALE)  # top of hammer head ≈ 121

        # Preferred: centred horizontally on hammer, bottom flush 10 px above head
        tx = wp.x() + hx_w - tw // 2
        ty = wp.y() + hy_w - th - 10

        # Clamp horizontally inside the available screen area
        tx = max(screen.left() + 8, min(tx, screen.right() - tw - 8))

        # If there is not enough room above, fall back to bottom-right corner
        if ty < screen.top() + 8:
            tx = screen.right()  - tw - 16
            ty = screen.bottom() - th - 16

        toast.move(tx, ty)
        toast.show()
        self._update_toast = toast  # prevent GC from destroying the window

    def _prompt_and_update(self):
        """Called when player clicks the 'new version' menu item."""
        if not self._pending_update:
            return
        tag = self._pending_update["tag"]
        url = self._pending_update["url"]
        from config import VERSION
        reply = QMessageBox.question(
            self, "發現新版本",
            f"目前版本：{VERSION}\n新版本：{tag}\n\n是否要現在下載並更新？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply == QMessageBox.Yes:
            self._do_update(tag, url)

    def _do_update(self, tag: str, url: str):
        """Start download in background; show a progress dialog."""
        if not getattr(sys, "frozen", False):
            QMessageBox.information(
                self, "更新",
                "目前以腳本模式執行，無法自動更新。\n請至 GitHub 手動下載最新版本。",
            )
            return

        import os
        import update as upd
        old_exe = upd.exe_path()
        new_exe = os.path.join(os.path.dirname(old_exe), "BlacksmithWidget_new.exe")
        self._update_old_exe = old_exe
        self._update_new_exe = new_exe

        # Build progress dialog
        dlg = QDialog(self)
        dlg.setWindowTitle("更新中")
        dlg.setWindowFlags(
            Qt.WindowStaysOnTopHint | Qt.Dialog |
            Qt.CustomizeWindowHint | Qt.WindowTitleHint
        )
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(8)
        lbl = QLabel(f"正在下載 {tag}…")
        lbl.setWordWrap(True)
        layout.addWidget(lbl)
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setTextVisible(True)
        layout.addWidget(bar)
        dlg.setFixedSize(300, 88)
        self._update_dlg     = dlg
        self._update_dlg_lbl = lbl        # kept so _on_dl_done can update text
        self._dl_progress.connect(bar.setValue)
        dlg.show()

        def _dl():
            ok = upd.download_exe(url, new_exe,
                                  progress_cb=self._dl_progress.emit)
            self._dl_done.emit(ok)

        threading.Thread(target=_dl, daemon=True).start()

    def _on_dl_done(self, success: bool):
        try:
            self._dl_progress.disconnect()
        except Exception:
            pass

        if success:
            self._pending_update = None   # update applied — clear flag
            # ── Show countdown in the existing dialog — no extra click needed ──
            if self._update_dlg_lbl:
                self._update_dlg_lbl.setText(
                    "✅  下載完成！\n"
                    "遊戲將在 3 秒後自動重啟套用更新，\n"
                    "⚠️  無需手動操作。"
                )
            if self._update_dlg:
                self._update_dlg.setFixedSize(300, 112)
            self._countdown_n = 3
            self._countdown_timer = QTimer(self)
            self._countdown_timer.setInterval(1000)
            self._countdown_timer.timeout.connect(self._on_restart_countdown)
            self._countdown_timer.start()
        else:
            if self._update_dlg:
                self._update_dlg.close()
                self._update_dlg     = None
                self._update_dlg_lbl = None
            QMessageBox.warning(
                self, "下載失敗",
                "下載失敗，請稍後再試，或至 GitHub 手動下載最新版本。",
            )

    def _on_restart_countdown(self):
        """Called every second after a successful download; restarts at 0."""
        self._countdown_n -= 1
        if self._countdown_n > 0:
            if self._update_dlg_lbl:
                self._update_dlg_lbl.setText(
                    f"✅  下載完成！\n"
                    f"遊戲將在 {self._countdown_n} 秒後自動重啟套用更新，\n"
                    f"⚠️  無需手動操作。"
                )
        else:
            # Time's up — tear down and restart
            if self._countdown_timer:
                self._countdown_timer.stop()
                self._countdown_timer = None
            if self._update_dlg:
                self._update_dlg.close()
                self._update_dlg     = None
                self._update_dlg_lbl = None
            import update as upd
            upd.launch_updater(self._update_new_exe, self._update_old_exe)
            self.close()

    def _check_update_manual(self):
        """Context-menu manual check — if update already found, go straight to prompt."""
        if self._pending_update:
            self._prompt_and_update()
            return
        def _bg():
            import update as upd
            from config import VERSION
            info = upd.fetch_latest(timeout=8)
            if info is None:
                self._check_msg.emit("檢查更新", "無法連線至更新伺服器，請確認網路連線。")
            elif not upd.is_newer(info["tag"], VERSION):
                self._check_msg.emit("檢查更新", f"目前已是最新版本（{VERSION}）。")
            else:
                # Store pending + tell user the menu button has changed
                self._update_ready.emit(info["tag"], info["url"], False)
                self._check_msg.emit(
                    "發現新版本",
                    f"找到新版本 {info['tag']}！\n右鍵選單中已出現更新按鈕，點擊即可安裝。",
                )
        threading.Thread(target=_bg, daemon=True).start()

    def _restart(self):
        """Save state, launch a fresh instance, then close this one."""
        pos = self.pos()
        self.state.widget_x = pos.x()
        self.state.widget_y = pos.y()
        write_save(self.state.to_save())
        # Launch new instance — works for both .pyw script and frozen .exe
        if getattr(sys, "frozen", False):
            subprocess.Popen([sys.executable])
        else:
            subprocess.Popen([sys.executable] + sys.argv)
        self.close()

    # ── System tray ───────────────────────────────────────────────────────────

    def _setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            _wlog("[tray] system tray not available — skipping")
            return
        self._tray = QSystemTrayIcon(QIcon(self._make_tray_pixmap()), self)
        self._tray.setToolTip("鐵匠鋪小工具")

        menu = QMenu()

        s_act = QAction("⚙  設定", menu)
        s_act.triggered.connect(self._open_settings)
        menu.addAction(s_act)

        c_act = QAction("📌  移回螢幕中央", menu)
        c_act.triggered.connect(self._move_to_center)
        menu.addAction(c_act)

        top_act = QAction(
            "🔝  取消永遠置頂" if self.state.always_on_top else "🔝  永遠置頂",
            menu,
        )
        top_act.triggered.connect(self._toggle_always_on_top)
        menu.addAction(top_act)

        menu.addSeparator()

        r_act = QAction("🔄  重啟", menu)
        r_act.triggered.connect(self._restart)
        menu.addAction(r_act)

        q_act = QAction("✕  退出", menu)
        q_act.triggered.connect(self.close)
        menu.addAction(q_act)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()
        _wlog("[tray] shown")

    def _make_tray_pixmap(self) -> QPixmap:
        """Draw a 32×32 anvil icon programmatically — no external image needed."""
        from PyQt5.QtGui import QPainter as _P, QColor as _C, QBrush as _B
        from PyQt5.QtGui import QPen as _Pen, QPolygonF as _Poly
        from PyQt5.QtCore import QPointF as _Pt, QRectF as _R

        px = QPixmap(32, 32)
        px.fill(_C(0, 0, 0, 0))
        p = _P(px)
        p.setRenderHint(_P.Antialiasing)

        # Dark forge-brown background
        p.setPen(Qt.NoPen)
        p.setBrush(_B(_C(42, 30, 18, 240)))
        p.drawRoundedRect(_R(0, 0, 32, 32), 7, 7)

        # Anvil top face (trapezoid)
        p.setBrush(_B(_C(175, 175, 175)))
        face = _Poly([_Pt(4, 10), _Pt(24, 10), _Pt(22, 16), _Pt(6, 16)])
        p.drawPolygon(face)

        # Horn
        p.setBrush(_B(_C(145, 145, 145)))
        horn = _Poly([_Pt(24, 10), _Pt(29, 12), _Pt(24, 15)])
        p.drawPolygon(horn)

        # Waist / neck
        p.setBrush(_B(_C(105, 105, 105)))
        p.drawRect(_R(11, 16, 9, 4))

        # Base
        p.setBrush(_B(_C(140, 140, 140)))
        p.drawRoundedRect(_R(7, 20, 17, 6), 2, 2)

        # Highlight on top face edge
        p.setPen(_Pen(_C(215, 215, 215, 200), 1))
        p.drawLine(_Pt(5, 10), _Pt(23, 10))

        p.end()
        return px

    def _on_tray_activated(self, reason):
        """Double-click on tray icon raises the widget to the front."""
        if reason == QSystemTrayIcon.DoubleClick:
            self.showNormal()
            self.raise_()
            self.activateWindow()

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        self._save_timer.stop()
        self._timer.stop()
        if self._tray is not None:
            self._tray.hide()
        pos = self.pos()
        self.state.widget_x = pos.x()
        self.state.widget_y = pos.y()
        write_save(self.state.to_save())
        super().closeEvent(event)
        # Explicitly quit the event loop so aboutToQuit fires and os._exit(0) runs.
        # We cannot rely on setQuitOnLastWindowClosed because a dev-tools or
        # settings dialog may still be visible when the main widget closes.
        from PyQt5.QtWidgets import QApplication
        QApplication.instance().quit()
