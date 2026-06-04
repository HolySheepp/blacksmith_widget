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
from ui.settings    import _autostart_set, _autostart_migrate

try:
    from network        import NetworkClient
    from ui.peer_widget import PeerWidget
    from ui.multiplayer import MultiplayerDialog
    _MULTI_AVAILABLE = True
except ImportError:
    NetworkClient = None
    PeerWidget    = None
    MultiplayerDialog = None
    _MULTI_AVAILABLE = False

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
    _update_ready          = pyqtSignal(str, str, bool, str, str)  # (tag, url, show_toast, notes, page_url)
    _dl_progress           = pyqtSignal(int)             # download progress 0-100
    _dl_done               = pyqtSignal(bool)            # download finished (success?)
    _check_msg             = pyqtSignal(str, str)        # (title, body) info message
    _clear_pending         = pyqtSignal()                # clears _pending_update on main thread
    _prompt_update_requested = pyqtSignal()              # 背景執行緒通知主執行緒顯示更新對話框
    _update_api_failed     = pyqtSignal()                # API 無法連線，通知主執行緒顯示手動下載對話框

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
        _wlog("[init] autostart_migrate + autostart_set")
        _autostart_migrate()                   # one-time: remove legacy registry Run key if present
        _autostart_set(self.state.autostart)   # apply saved autostart preference on every launch
        # Apply saved always-on-top preference before first show() — no visible flicker
        if not self.state.always_on_top:
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool)

        # Restore last window position (saved in logical pixels)
        if self.state.widget_x is not None and self.state.widget_y is not None:
            self.move(self.state.widget_x, self.state.widget_y)

        _wlog("[init] KeyboardListener()")
        self.listener = KeyboardListener()
        self.listener.set_state(self.state)   # art-mode drag detection needs state
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
        # Fires after 400 ms to clear mouse_on_widget so the guide fades out.
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
        self._clear_pending.connect(lambda: setattr(self, '_pending_update', None))
        self._prompt_update_requested.connect(self._prompt_and_update)
        self._update_api_failed.connect(self._show_update_api_failed_dialog)

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

        # 監聽螢幕設定變動（插拔螢幕、切換投影模式）——確保 widget 不會消失在畫面外
        from PyQt5.QtWidgets import QApplication
        _desk = QApplication.desktop()
        _desk.screenCountChanged.connect(self._on_screen_config_changed)
        _desk.resized.connect(self._on_screen_config_changed)
        _wlog("[init] screen-change signals connected")

        # ── 多人模式 ──────────────────────────────────────────────────────────────
        self._net_client:   NetworkClient | None   = NetworkClient(self) if _MULTI_AVAILABLE else None
        self._peer_widgets: dict[str, PeerWidget]  = {}
        self._multi_dialog: MultiplayerDialog | None = None
        self._auto_rejoin_pending = False   # 啟動時等待自動重連的旗標
        self._rejoin_auto_create  = False   # ROOM_NOT_FOUND 時自動創建的旗標

        # ── 自動重連系統（被迫斷線時）────────────────────────────────────────
        self._was_in_room: bool    = False  # 是否曾在房間中（判斷是否被迫斷線）
        self._auto_recon_active    = False
        self._auto_recon_attempts  = 0
        self._auto_recon_timer     = QTimer(self)
        self._auto_recon_timer.setInterval(60_000)   # 60 秒一次
        self._auto_recon_timer.timeout.connect(self._on_auto_recon_tick)

        # 自己的聊天氣泡（顯示在 widget 上方）
        self._own_bubble_text:  str   = ""
        self._own_bubble_alpha: float = 0.0
        self._own_bubble_hold  = QTimer(self)
        self._own_bubble_hold.setSingleShot(True)
        self._own_bubble_hold.setInterval(5000)
        self._own_bubble_hold.timeout.connect(self._start_own_bubble_fade)
        self._own_bubble_fade  = QTimer(self)
        self._own_bubble_fade.setInterval(100)
        self._own_bubble_fade.timeout.connect(self._fade_own_bubble)

        # 聊天輸入框（獨立的 frameless QLineEdit 視窗，定位在 widget 正上方）
        if _MULTI_AVAILABLE:
            from PyQt5.QtWidgets import QLineEdit
            self._chat_le = QLineEdit()
            self._chat_le.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
            self._chat_le.setAttribute(Qt.WA_ShowWithoutActivating)
            self._chat_le.setPlaceholderText("說點什麼吧!")
            self._chat_le.setFixedHeight(28)
            self._chat_le.setStyleSheet(
                "QLineEdit { background: rgba(0,0,0,160); color: white; "
                "border: 1px solid rgba(255,255,255,80); border-radius: 4px; "
                "padding: 2px 8px; font-size: 12px; }"
            )
            self._chat_le.returnPressed.connect(self._on_chat_submitted)
            self._chat_le.installEventFilter(self)   # 偵測失焦/懸停
            self._chat_le_visible = False
            self._chat_input_hovered = False  # 滑鼠是否停留在輸入框上
        else:
            self._chat_le = None
            self._chat_le_visible = False

        # 幀廣播計時器（20fps；在 _start_timers 中啟動，只在房間中才廣播）
        self._frame_timer = QTimer(self)
        self._frame_timer.setInterval(50)
        self._frame_timer.timeout.connect(self._broadcast_frame)

        # 連接 NetworkClient signals
        if self._net_client is not None:
            self._net_client.room_joined.connect(self._on_room_joined)
            self._net_client.room_left.connect(self._on_room_left)
            self._net_client.player_joined.connect(self._on_player_joined)
            self._net_client.player_left.connect(self._on_player_left)
            self._net_client.kicked.connect(self._on_kicked_from_room)
            self._net_client.room_dissolved.connect(self._on_room_dissolved)
            self._net_client.frame_received.connect(self._on_frame_received)
            self._net_client.chat_received.connect(self._on_chat_received)
            self._net_client.connected.connect(self._on_net_connected)
            self._net_client.disconnected.connect(self._on_net_disconnected)
            self._net_client.connection_dropped.connect(self._on_net_connection_dropped)
            self._net_client.conn_error.connect(self._on_net_conn_error)
            self._net_client.server_error.connect(self._on_net_server_error)
            self._net_client.server_notice.connect(self._on_server_notice)

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
        self._frame_timer.start()
        # 若有上次房間記錄，3 秒後靜默嘗試自動重連
        if (_MULTI_AVAILABLE and self._net_client is not None
                and self.state.mp_server_host
                and self.state.mp_room_id
                and self.state.mp_player_name):
            QTimer.singleShot(3000, self._try_auto_rejoin)
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
            self.raise_()
            self.activateWindow()

    def _on_screen_config_changed(self, *_):
        """Called when screens are added/removed or resized (e.g. projection mode switch).
        Waits 500 ms for the OS to finish reconfiguring displays, then snaps widget back."""
        QTimer.singleShot(500, self._ensure_on_screen)

    def _move_to_center(self):
        """Move widget to the centre of whichever screen it currently overlaps,
        then temporarily raise it to the front (does NOT re-enable always-on-top)."""
        from PyQt5.QtWidgets import QApplication
        geo = QApplication.desktop().availableGeometry(self)
        self.move(geo.center() - self.rect().center())
        self.raise_()
        self.activateWindow()

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
        # 自己的聊天氣泡（widget 座標，不受 ui_scale 影響）
        if self._own_bubble_alpha > 0.01 and self._own_bubble_text:
            self._draw_own_bubble(painter)
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
        # 只在房間中才顯示聊天輸入框
        if self._net_client and self._net_client.current_room:
            self._show_chat_input()
        else:
            self._hide_chat_input()

    def leaveEvent(self, event):
        # Don't hide immediately — give the player 400 ms to reach the ghost circle.
        self._ghost_hide_timer.start()
        super().leaveEvent(event)
        QTimer.singleShot(200, self._hide_chat_input)

    def moveEvent(self, event):
        super().moveEvent(event)
        if self._chat_le_visible:
            self._reposition_chat_input()

    def eventFilter(self, obj, event):
        from PyQt5.QtCore import QEvent
        if obj is self._chat_le:
            t = event.type()
            if t == QEvent.FocusOut:
                # 失焦後延遲隱藏，讓使用者有時間移回 widget
                QTimer.singleShot(200, self._hide_chat_input)
            elif t == QEvent.Enter:
                # 滑鼠進入輸入框：標記懸停，取消待定的隱藏
                self._chat_input_hovered = True
            elif t == QEvent.Leave:
                # 滑鼠離開輸入框：延遲隱藏
                self._chat_input_hovered = False
                QTimer.singleShot(200, self._hide_chat_input)
        return super().eventFilter(obj, event)

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

    def _open_multiplayer(self):
        if not _MULTI_AVAILABLE:
            return
        if self._multi_dialog is not None and self._multi_dialog.isVisible():
            self._multi_dialog.raise_()
            self._multi_dialog.activateWindow()
            return
        dlg = MultiplayerDialog(self._net_client, self.state,
                                lerp_changed_cb=self._on_lerp_changed,
                                parent=self)
        self._multi_dialog = dlg
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

        if _MULTI_AVAILABLE:
            multi_act = QAction("👥  多人（實驗）", self)
            multi_act.triggered.connect(self._open_multiplayer)
            menu.addAction(multi_act)

        menu.addSeparator()

        if self._pending_update:
            tag        = self._pending_update["tag"]
            update_act = QAction(f"🆕  檢測到新版本 {tag}  點我更新！", self)
        else:
            update_act = QAction("🔍  檢查更新", self)
        # Always re-check the API before prompting — ensures we don't act on a
        # stale cached version (e.g. v0.3.5 cached, but v0.4.1 now available).
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
        s.charge_prefire      = False

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
        # Keep tray menu text in sync (tray menu is built once, not rebuilt each open)
        if hasattr(self, '_tray_top_act') and self._tray_top_act is not None:
            self._tray_top_act.setText(
                "🔝  取消永遠置頂" if self.state.always_on_top else "🔝  永遠置頂"
            )

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
            self._update_ready.emit(
                info["tag"], info["url"], is_startup,
                info.get("notes", ""), info.get("page_url", ""),
            )

    def _on_update_ready(self, tag: str, url: str, show_toast: bool, notes: str, page_url: str = ""):
        """Main thread: store pending update; optionally show toast bubble."""
        self._pending_update = {"tag": tag, "url": url, "notes": notes, "page_url": page_url}
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
        """Called when player clicks the 'new version' menu item.
        Shows a dialog with release notes and Yes/No buttons."""
        if not self._pending_update:
            return
        tag   = self._pending_update["tag"]
        url   = self._pending_update["url"]
        notes = self._pending_update.get("notes", "")
        from config import VERSION
        from PyQt5.QtWidgets import QTextBrowser, QDialogButtonBox

        dlg = QDialog(self)
        dlg.setWindowTitle("🆕  發現新版本")
        dlg.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.Dialog)
        dlg.setMinimumWidth(420)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(10)
        layout.setContentsMargins(14, 12, 14, 14)

        # Version header
        header = QLabel(f"<b>目前版本：</b>{VERSION}　→　<b>新版本：</b>{tag}")
        header.setTextFormat(Qt.RichText)
        layout.addWidget(header)

        # Release notes (Markdown rendered via QTextBrowser)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setReadOnly(True)
        if notes.strip():
            browser.setMarkdown(notes)
        else:
            browser.setPlainText("（沒有附上更新說明）")
        browser.setMinimumHeight(160)
        browser.setMaximumHeight(340)   # cap height; overflow scrolls inside the browser
        layout.addWidget(browser)

        # Buttons
        buttons = QDialogButtonBox()
        btn_update = buttons.addButton("立即更新", QDialogButtonBox.AcceptRole)
        btn_later  = buttons.addButton("稍後",     QDialogButtonBox.RejectRole)
        btn_update.setDefault(True)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)

        if dlg.exec_() == QDialog.Accepted:
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
            self._show_dl_failed_dialog()

    def _show_update_api_failed_dialog(self):
        """手動點擊「檢查更新」但 API 無法連線時，提供瀏覽器開啟 releases 頁面的按鈕。"""
        import webbrowser
        import update as upd
        from PyQt5.QtWidgets import QDialogButtonBox

        page_url = upd.releases_page()

        dlg = QDialog(self)
        dlg.setWindowTitle("檢查更新")
        dlg.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.Dialog)
        dlg.setMinimumWidth(360)

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(14, 12, 14, 14)
        lay.setSpacing(10)

        msg = QLabel(
            "無法連線至更新伺服器。\n\n"
            "可能原因：公司網路限制、無網路連線。\n\n"
            "點擊下方按鈕可用瀏覽器開啟 GitHub releases 頁面，\n"
            "手動查看並下載最新版本。"
        )
        msg.setWordWrap(True)
        lay.addWidget(msg)

        buttons = QDialogButtonBox()
        btn_open = buttons.addButton("🌐  開啟 GitHub 下載頁面", QDialogButtonBox.AcceptRole)
        btn_open.clicked.connect(lambda: webbrowser.open(page_url))
        btn_close = buttons.addButton("關閉", QDialogButtonBox.RejectRole)
        btn_close.clicked.connect(dlg.reject)
        lay.addWidget(buttons)

        dlg.exec_()

    def _show_dl_failed_dialog(self):
        """下載失敗時顯示對話框，提供在瀏覽器手動下載的按鈕。"""
        import webbrowser
        import update as upd
        from PyQt5.QtWidgets import QDialogButtonBox

        pu = self._pending_update  # {"tag", "url", "page_url", "notes"}
        # page_url 優先用 API 回傳值，API 失敗時 fallback 到硬編碼的 releases 頁面
        page_url = (pu.get("page_url") if pu else None) or upd.releases_page()
        tag      = pu.get("tag", "") if pu else ""

        dlg = QDialog(self)
        dlg.setWindowTitle("下載失敗")
        dlg.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.Dialog)
        dlg.setMinimumWidth(360)

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(14, 12, 14, 14)
        lay.setSpacing(10)

        msg = QLabel(
            "自動下載失敗（可能是公司網路 SSL 限制）。\n\n"
            "請點擊下方按鈕，用瀏覽器手動下載最新版本：\n"
            f"瀏覽器使用系統憑證，可正常通過公司 Proxy。"
        )
        msg.setWordWrap(True)
        lay.addWidget(msg)

        buttons = QDialogButtonBox()
        if page_url:
            btn_open = buttons.addButton(
                f"🌐  在瀏覽器開啟下載頁面 {tag}",
                QDialogButtonBox.AcceptRole,
            )
            btn_open.clicked.connect(lambda: webbrowser.open(page_url))
        btn_close = buttons.addButton("關閉", QDialogButtonBox.RejectRole)
        btn_close.clicked.connect(dlg.reject)
        lay.addWidget(buttons)

        dlg.exec_()

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
        """Context-menu: always do a fresh API call before acting.
        This prevents acting on a stale cached version — e.g. the background
        check cached v0.3.5, but v0.4.1 has since been published."""
        def _bg():
            import update as upd
            from config import VERSION
            from PyQt5.QtCore import QMetaObject, Qt
            info = upd.fetch_latest(timeout=8)
            if info is None:
                # API 無法連線 → 通知主執行緒顯示含「手動下載」按鈕的對話框
                self._update_api_failed.emit()
            elif not upd.is_newer(info["tag"], VERSION):
                # Already up-to-date — clear stale pending via signal (thread-safe)
                self._clear_pending.emit()
                self._check_msg.emit("檢查更新", f"目前已是最新版本（{VERSION}）。")
            else:
                # Refresh _pending_update with the latest data from the API,
                # then open the update prompt on the main thread.
                # QueuedConnection guarantees _update_ready signal is processed
                # (i.e. _pending_update is written) before _prompt_and_update runs.
                self._update_ready.emit(
                    info["tag"], info["url"], False,
                    info.get("notes", ""), info.get("page_url", ""),
                )
                self._prompt_update_requested.emit()
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

        self._tray_top_act = QAction(
            "🔝  取消永遠置頂" if self.state.always_on_top else "🔝  永遠置頂",
            menu,
        )
        self._tray_top_act.triggered.connect(self._toggle_always_on_top)
        menu.addAction(self._tray_top_act)

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

    # ── 多人模式方法 ──────────────────────────────────────────────────────────

    def _broadcast_frame(self):
        if self._net_client is None:
            return
        if not self._net_client.is_connected:
            return
        # 即使不在房間也廣播，讓伺服器端能即時看到玩家統計資料
        # 伺服器只在玩家有房間時才轉播給其他人
        s = self.state
        charge = (s.typing_charge / max(1, s.typing_max_charge)
                  if s.kb_mode == "charge" else 0.0)
        # 金屬塊狀態
        _m = getattr(s, "current_metal", None)
        if _m is not None and not _m.dead:
            metal_type     = _m.type_idx
            metal_ratio    = float(_m.ratio)
            metal_spawn_t  = float(_m.spawn_t)
            metal_flash_t  = float(_m.flash_t)
            metal_complete = bool(_m.complete)
        else:
            metal_type     = -1
            metal_ratio    = 0.0
            metal_spawn_t  = 0.0
            metal_flash_t  = 0.0
            metal_complete = False
        data = {
            "vcx":           s.vcx,
            "vcy":           s.vcy,
            "vcvx":          s.vcvx,
            "vcvy":          s.vcvy,
            "has_hit":       s.has_hit,
            "anvil_glow":    s.anvil_glow,
            "kb_state":      s.kb_state,
            "kb_active":     s.kb_active,
            "kb_mode":       s.kb_mode,
            "turbo_mode":    s.turbo_mode,
            "fever_active":  s.fever_active,
            "strike_color":  list(s.strike_color),
            "hit_count":     s.hit_count,
            "click_count":   s.click_count,
            "force_count":   s.force_count,
            "play_time":     s.play_time,
            "forge_counts":  list(getattr(s, "forge_counts", [])),
            "charge":        charge,
            # hide_anvil 不廣播——每位玩家在自己螢幕上自行決定是否隱藏對方鐵砧
            "ui_scale":      s.ui_scale,
            "metal_type":    metal_type,
            "metal_ratio":   metal_ratio,
            "metal_spawn_t": metal_spawn_t,
            "metal_flash_t": metal_flash_t,
            "metal_complete": metal_complete,
        }
        self._net_client.send_frame(data)

    def _on_room_joined(self, room_id: str, players: list, host: str):
        """成功加入或創建房間——為其他玩家建立 PeerWidget。"""
        # 清除自動創建旗標
        self._rejoin_auto_create = False
        # 標記「曾在房間」並停止自動重連計時器
        self._was_in_room = True
        self._stop_auto_reconnect()
        # 儲存房間資訊以供下次啟動自動重連
        if self._net_client is not None:
            my_name = self._net_client.player_name or ""
            self.state.mp_room_id      = room_id
            self.state.mp_player_name  = my_name
            self.state.mp_server_host  = self._net_client.server_host
        # 關閉舊的 peer widgets（若有）
        self._close_all_peer_widgets()
        my_name = self._net_client.player_name if self._net_client else None
        for name in players:
            if name != my_name:
                self._create_peer_widget(name)

    def _on_player_joined(self, name: str):
        my_name = self._net_client.player_name if self._net_client else None
        if name != my_name and name not in self._peer_widgets:
            self._create_peer_widget(name)

    def _on_player_left(self, name: str):
        pw = self._peer_widgets.pop(name, None)
        if pw is not None:
            pw.close()

    def _on_kicked_from_room(self):
        # 被踢除：主動（admin 行為），不觸發自動重連
        self._was_in_room = False
        self._stop_auto_reconnect()
        self.state.mp_room_id = ""
        self.state.mp_player_name = ""
        self.state.mp_server_host = ""
        self._close_all_peer_widgets()
        self._hide_chat_input()

    def _on_room_left(self):
        """玩家主動退出房間：不觸發自動重連。"""
        self._was_in_room = False
        self._stop_auto_reconnect()
        self._close_all_peer_widgets()
        self._hide_chat_input()

    def _on_room_dissolved(self):
        # 管理員解散：不觸發自動重連
        self._was_in_room = False
        self._stop_auto_reconnect()
        self._close_all_peer_widgets()
        self._hide_chat_input()

    def _on_frame_received(self, from_name: str, data: dict):
        pw = self._peer_widgets.get(from_name)
        if pw is not None:
            pw.update_from_frame(data)

    def _on_chat_received(self, from_name: str, text: str):
        my_name = self._net_client.player_name if self._net_client else None
        if from_name == my_name:
            # 自己發的訊息 → 顯示在自己的 widget 上方
            self._show_own_bubble(text)
        else:
            pw = self._peer_widgets.get(from_name)
            if pw is not None:
                pw.show_bubble(text)

    def _create_peer_widget(self, name: str):
        if not _MULTI_AVAILABLE:
            return
        pw = PeerWidget(player_name=name)
        pw.set_lerp(self.state.mp_lerp)   # 繼承目前的 lerp 設定
        pw.show()
        self._peer_widgets[name] = pw

    def _close_all_peer_widgets(self):
        for pw in self._peer_widgets.values():
            pw.close()
        self._peer_widgets.clear()

    def _on_net_connected(self):
        """NetworkClient 連線成功時呼叫：若為自動重連，送出加入房間請求。"""
        if self._auto_rejoin_pending:
            self._do_auto_rejoin()

    def _on_net_connection_dropped(self):
        """WebSocket 意外中斷（重試前立即觸發）：立即關閉 peer widgets。
        若玩家曾在房間，啟動自動重連系統（60s × 10 次）。"""
        self._close_all_peer_widgets()
        self._hide_chat_input()
        # 只有「本來在房間且尚未啟動重連」才觸發
        if self._was_in_room and not self._auto_recon_active:
            self._start_auto_reconnect()

    def _on_server_notice(self, text: str):
        """伺服器廣播公告——以托盤氣泡通知顯示，讓玩家注意到。"""
        if self._tray is not None:
            self._tray.showMessage(
                "📢  伺服器公告", text,
                QSystemTrayIcon.Information, 10_000)

    # ── 自動重連（被迫斷線） ──────────────────────────────────────────────────

    def _start_auto_reconnect(self):
        """啟動 60s 間隔、最多 10 次的自動重連，並以托盤氣泡通知用戶。"""
        self._auto_recon_active   = True
        self._auto_recon_attempts = 0
        if self._tray is not None:
            self._tray.showMessage(
                "🔌  已掉線",
                "伺服器關閉或在進行維護更新，將自動嘗試重新連接",
                QSystemTrayIcon.Warning, 8_000)
        self._auto_recon_timer.start()

    def _stop_auto_reconnect(self):
        """取消自動重連。"""
        self._auto_recon_active = False
        self._auto_recon_timer.stop()

    def _on_auto_recon_tick(self):
        """每 60 秒觸發：嘗試重連一次。"""
        if self._auto_recon_attempts >= 10:
            self._stop_auto_reconnect()
            if self._tray is not None:
                self._tray.showMessage(
                    "❌  無法重新連接",
                    "無法重新連接到伺服器，請聯繫一加",
                    QSystemTrayIcon.Critical, 10_000)
            return
        self._auto_recon_attempts += 1
        # 若已連線（之前快速重試成功但沒有進房間）→ 直接加入
        if self._net_client and self._net_client.is_connected:
            self._do_auto_rejoin()
        else:
            self._try_auto_rejoin()

    # ── Lerp 回調 ─────────────────────────────────────────────────────────────

    def _on_lerp_changed(self, enabled: bool):
        """進階設定切換 lerp 時，即時更新所有現有 peer widgets。"""
        for pw in self._peer_widgets.values():
            pw.set_lerp(enabled)

    def _on_net_conn_error(self, _msg: str):
        """連線失敗（非重試斷線）——若為自動重連嘗試，顯示氣泡通知。"""
        if self._auto_rejoin_pending:
            self._auto_rejoin_pending = False
            note = ("連線失敗，無法加入上次遊玩所在房間，"
                    "請檢查網路連線，目前為單人模式")
            if self._tray is not None:
                self._tray.showMessage(
                    "鐵匠鋪小工具", note,
                    QSystemTrayIcon.Warning, 6000)

    def _on_net_disconnected(self, reason: str):
        """伺服器斷線（重試 3 次失敗）時，清除 peer widgets 和輸入框。
        不清除 mp_* 存檔欄位，保留以便重連後自動重加入。"""
        self._close_all_peer_widgets()
        self._hide_chat_input()

    def _try_auto_rejoin(self):
        """啟動時靜默嘗試重連並加入上次的房間。"""
        if (not _MULTI_AVAILABLE or self._net_client is None
                or not self.state.mp_server_host
                or not self.state.mp_room_id
                or not self.state.mp_player_name):
            return
        if self._net_client.current_room:
            return   # 已在房間中（理論上不應發生）
        if self._net_client.is_connected:
            # 已連線，直接嘗試加入
            self._do_auto_rejoin()
        else:
            # 尚未連線，先連線（連線成功後 _on_net_connected 會自動呼叫 _do_auto_rejoin）
            self._auto_rejoin_pending = True
            self._net_client.connect_to_server(
                self.state.mp_server_host,
                self.state.mp_port,
            )

    def _do_auto_rejoin(self):
        """連線後發送 set_name + join_room（靜默）。
        若房間不存在（ROOM_NOT_FOUND）則 _on_net_server_error 自動 create_room。"""
        self._auto_rejoin_pending = False
        if self._net_client is None or self._net_client.current_room:
            return
        self._net_client.set_name(self.state.mp_player_name)
        self._rejoin_auto_create = True
        self._net_client.join_room(self.state.mp_room_id)

    def _on_net_server_error(self, code: str, _msg: str):
        """伺服器錯誤：若自動重連時收到 ROOM_NOT_FOUND，自動創建房間。"""
        if (code == "ROOM_NOT_FOUND"
                and self._rejoin_auto_create
                and self._net_client is not None
                and self._net_client.is_connected
                and not self._net_client.current_room
                and self.state.mp_room_id):
            self._rejoin_auto_create = False
            self._net_client.create_room(self.state.mp_room_id)
        else:
            self._rejoin_auto_create = False

    def _reposition_chat_input(self):
        """把聊天輸入框定位在鐵砧面上方約 120px 處，寬度為 widget 一半，水平置中。"""
        if self._chat_le is None:
            return
        from config import FACE_TOP
        chat_w = max(80, self.width() // 2)
        self._chat_le.setFixedWidth(chat_w)
        face_top_px = int(FACE_TOP * self.state.ui_scale)   # 鐵砧面在 widget 中的 y
        offset_y = face_top_px - 120                        # 距鐵砧面上方 120px（widget 座標）
        pos = self.mapToGlobal(QPoint(0, 0))
        chat_x = pos.x() + (self.width() - chat_w) // 2
        chat_y = pos.y() + offset_y
        self._chat_le.move(chat_x, chat_y)

    def _show_chat_input(self):
        if self._chat_le is None or self._chat_le_visible:
            return
        if self._net_client is None or self._net_client.current_room is None:
            return
        self._reposition_chat_input()
        self._chat_le.show()
        self._chat_le_visible = True

    def _hide_chat_input(self):
        if self._chat_le is None or not self._chat_le_visible:
            return
        # 若輸入框有焦點或滑鼠懸停，不立即隱藏
        if self._chat_le.hasFocus() or self._chat_input_hovered:
            return
        self._chat_le.hide()
        self._chat_le_visible = False

    def _on_chat_submitted(self):
        if self._chat_le is None or self._net_client is None:
            return
        text = self._chat_le.text().strip()
        if not text:
            return
        self._chat_le.clear()
        self._net_client.send_chat(text)
        # 本地也顯示（伺服器會廣播回來，但本地端先預覽）
        # 實際上伺服器廣播回來時 _on_chat_received 會處理，所以這裡不必重複

    def _show_own_bubble(self, text: str):
        self._own_bubble_text  = text
        self._own_bubble_alpha = 1.0
        self._own_bubble_hold.start()        # 5 秒後開始淡出
        self._own_bubble_fade.stop()

    def _start_own_bubble_fade(self):
        self._own_bubble_fade.start()

    def _fade_own_bubble(self):
        self._own_bubble_alpha = max(0.0, self._own_bubble_alpha - 0.08)
        if self._own_bubble_alpha <= 0.01:
            self._own_bubble_alpha = 0.0
            self._own_bubble_text  = ""
            self._own_bubble_fade.stop()
        self.update()

    def _draw_own_bubble(self, painter: QPainter):
        from PyQt5.QtGui import QColor, QPen, QBrush, QFont, QPainterPath
        from PyQt5.QtCore import QRectF
        import math

        text  = self._own_bubble_text
        alpha = self._own_bubble_alpha  # 0.0–1.0

        painter.save()
        # 字型
        font = QFont("Segoe UI", 11)
        painter.setFont(font)
        fm = painter.fontMetrics()

        # 計算文字寬高（限制最大寬度 260px）
        max_w = min(260, self.width() - 20)
        # 使用 boundingRect 計算文字在限定寬度內的高度
        from PyQt5.QtCore import Qt as _Qt
        text_rect = painter.fontMetrics().boundingRect(
            0, 0, max_w, 200, _Qt.TextWordWrap, text
        )
        tw = text_rect.width()
        th = text_rect.height()

        pad_x, pad_y = 12, 8
        bw = tw + pad_x * 2
        bh = th + pad_y * 2

        # 氣泡定位在 widget 頂端往上（外面，需換算）
        # 因為這在 paintEvent 裡用 widget 座標，所以畫在 widget 頂部內側
        bx = (self.width() - bw) / 2
        by = 6.0   # 距 widget 頂端 6px

        a8 = int(alpha * 255)

        # 背景
        painter.setPen(QPen(QColor(255, 255, 255, int(alpha * 60)), 1))
        painter.setBrush(QBrush(QColor(0, 0, 0, int(alpha * 170))))
        painter.drawRoundedRect(QRectF(bx, by, bw, bh), 8, 8)

        # 文字
        painter.setPen(QColor(255, 255, 255, a8))
        painter.drawText(
            QRectF(bx + pad_x, by + pad_y, tw, th),
            _Qt.TextWordWrap, text
        )
        painter.restore()

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        # 關閉所有 peer widgets 和輸入框
        self._close_all_peer_widgets()
        if self._chat_le is not None:
            self._chat_le.close()
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
