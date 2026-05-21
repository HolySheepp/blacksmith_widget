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

from PyQt5.QtWidgets import QWidget, QMenu, QAction, QDialog, QVBoxLayout, QLabel, QMessageBox, QProgressBar
from PyQt5.QtCore    import Qt, QTimer, QPoint, pyqtSlot, pyqtSignal
from PyQt5.QtGui     import QPainter

from config         import WIDGET_W, WIDGET_H
from game.state     import GameState
from ui.renderer    import draw_frame
from ui.devtools    import DevToolsDialog
from ui.settings    import SettingsDialog
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

# ── Dev Tools trigger triangle ────────────────────────────────────────────────
# User specified widget-space vertices at scale=0.5:
#   A=(255,172)  B=(255,192)  C=(285,181)
# Converting to game-space (÷0.5) so the zone scales with ui_scale:
_DT_GAME = [(510, 344), (510, 384), (570, 362)]
_DT_CLICKS_NEEDED = 5
_DT_INTERVAL_SEC  = 1.0


def _in_dt_zone(px: float, py: float, scale: float) -> bool:
    """Point-in-triangle test; triangle vertices scale with ui_scale."""
    verts = [(gx * scale, gy * scale) for gx, gy in _DT_GAME]
    (ax, ay), (bx, by), (cx, cy) = verts

    def cross(p1x, p1y, p2x, p2y):
        return (p2x - p1x) * (py - p1y) - (p2y - p1y) * (px - p1x)

    d1 = cross(ax, ay, bx, by)
    d2 = cross(bx, by, cx, cy)
    d3 = cross(cx, cy, ax, ay)
    has_neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
    has_pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
    return not (has_neg and has_pos)


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
        _wlog("[init] QTimer.singleShot(0) — all starts deferred to _start_timers()")
        QTimer.singleShot(0, self._start_timers)

        # Drag state
        self._press_global: QPoint | None = None
        self._drag_offset:  QPoint | None = None
        self._is_dragging:  bool          = False

        # Dev Tools trigger
        self._dt_clicks: int   = 0
        self._dt_last_t: float = 0.0
        self._dt_dialog: DevToolsDialog | None = None

        # Settings dialog
        self._settings_dialog: SettingsDialog | None = None

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
            self._press_global = event.globalPos()
            self._drag_offset  = event.globalPos() - self.frameGeometry().topLeft()
            self._is_dragging  = False
        elif event.button() == Qt.RightButton:
            self._show_context_menu(event.globalPos())

    def mouseMoveEvent(self, event):
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
            if not self._is_dragging:
                self._check_dt_trigger(event.pos())
            self._is_dragging  = False
            self._press_global = None
            self._drag_offset  = None

    # ── Dev Tools trigger ─────────────────────────────────────────────────────

    def _check_dt_trigger(self, pos):
        x, y = pos.x(), pos.y()
        if _in_dt_zone(x, y, self.state.ui_scale):
            now = time.monotonic()
            if now - self._dt_last_t > _DT_INTERVAL_SEC:
                self._dt_clicks = 1
            else:
                self._dt_clicks += 1
            self._dt_last_t = now
            if self._dt_clicks >= _DT_CLICKS_NEEDED:
                self._dt_clicks = 0
                self._open_devtools()
        else:
            self._dt_clicks = 0

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

    def _open_settings(self):
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return
        dlg = SettingsDialog(self.state, self)
        self._settings_dialog = dlg
        self._place_dialog(dlg)

    # ── Context menu ──────────────────────────────────────────────────────────

    def _show_context_menu(self, global_pos: QPoint):
        menu = QMenu(self)
        s = self.state

        in_fever    = s.turbo_mode and s.fever_active
        in_special  = s.turbo_mode or s.kb_mode == "charge_legacy"
        if in_fever:
            mode_label = "🔥  Fever 進行中（無法切換）"
        elif s.turbo_mode:
            mode_label = "⚡  渦輪模式（請從設定切換）"
        elif s.kb_mode == "charge_legacy":
            mode_label = "🔨  蓄力(舊版)模式（請從設定切換）"
        elif s.kb_mode == "charge":
            mode_label = "⚡  切換為連打模式"
        else:
            mode_label = "🔥  切換為蓄力模式"

        toggle = QAction(mode_label, self)
        toggle.triggered.connect(self._toggle_mode)
        toggle.setEnabled(not in_fever and not in_special)
        menu.addAction(toggle)

        menu.addSeparator()

        settings_act = QAction("⚙  設定", self)
        settings_act.triggered.connect(self._open_settings)
        menu.addAction(settings_act)

        menu.addSeparator()

        center_act = QAction("📌  移回螢幕中央", self)
        center_act.triggered.connect(self._move_to_center)
        menu.addAction(center_act)

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

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        self._save_timer.stop()
        self._timer.stop()
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
