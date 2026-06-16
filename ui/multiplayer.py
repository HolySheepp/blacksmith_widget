"""
MultiplayerDialog — 多人模式非模態對話框。
依循 devtools.py 的風格：QVBoxLayout, QGroupBox, QFormLayout, 間距 10px, 邊距 12px。
"""
from __future__ import annotations

from PyQt5.QtCore import Qt, QRegExp
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QGroupBox, QListWidget, QListWidgetItem,
    QFrame, QCheckBox,
)
from PyQt5.QtGui import QRegExpValidator, QIntValidator


# ── 進階設定對話框 ─────────────────────────────────────────────────────────────

class MultiplayerAdvancedDialog(QDialog):
    """多人模式進階設定：伺服器 IP、Port、連線按鈕、lerp 補幀。"""

    def __init__(self, network_client, state, lerp_changed_cb, parent=None):
        super().__init__(parent)
        self._client = network_client
        self._state  = state
        self._lerp_cb = lerp_changed_cb  # callback(bool) 當 lerp 切換時通知 widget.py

        self.setWindowTitle("多人模式進階設定")
        self.setWindowFlags(Qt.Dialog)
        self.setMinimumWidth(360)
        self._build_ui()
        self._connect_signals()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(12, 12, 12, 12)

        # ── 連線設定 ──────────────────────────────────────────────────────────
        conn_box = QGroupBox("連線設定")
        conn_lay = QVBoxLayout(conn_box)
        conn_lay.setSpacing(6)

        ip_row = QHBoxLayout()
        ip_row.addWidget(QLabel("伺服器 IP："))
        self._ip_edit = QLineEdit()
        self._ip_edit.setPlaceholderText("172.20.39.180")
        ip_row.addWidget(self._ip_edit)
        conn_lay.addLayout(ip_row)

        port_row = QHBoxLayout()
        port_row.addWidget(QLabel("Port："))
        self._port_edit = QLineEdit()
        self._port_edit.setValidator(QIntValidator(1, 65535, self._port_edit))
        self._port_edit.setFixedWidth(70)
        port_row.addWidget(self._port_edit)
        port_row.addStretch()
        conn_lay.addLayout(port_row)

        self._adv_conn_btn = QPushButton("連線")
        self._adv_conn_btn.clicked.connect(self._on_adv_conn_clicked)
        conn_lay.addWidget(self._adv_conn_btn)

        # 連線狀態提示
        self._adv_status_lbl = QLabel("")
        self._adv_status_lbl.setStyleSheet("color: red;")
        self._adv_status_lbl.setWordWrap(True)
        self._adv_status_lbl.hide()
        conn_lay.addWidget(self._adv_status_lbl)

        root.addWidget(conn_box)

        # ── 動畫設定 ──────────────────────────────────────────────────────────
        anim_box = QGroupBox("動畫設定")
        anim_lay = QVBoxLayout(anim_box)
        anim_lay.setSpacing(4)

        self._lerp_chk = QCheckBox("啟用多人動畫補幀（lerp）")
        anim_lay.addWidget(self._lerp_chk)

        lerp_hint = QLabel(
            "⚠  補幀會用插值平滑對方的位置，\n"
            "   在高延遲環境下可讓對方鎚子動作更流暢。"
        )
        lerp_hint.setStyleSheet("color: gray; font-size: 11px;")
        lerp_hint.setWordWrap(True)
        anim_lay.addWidget(lerp_hint)

        root.addWidget(anim_box)

        # ── 關閉按鈕 ──────────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        root.addWidget(sep)

        close_btn = QPushButton("關閉")
        close_btn.clicked.connect(self.close)
        root.addWidget(close_btn)

        # ── 填入預存值 ────────────────────────────────────────────────────────
        if self._state:
            if self._state.mp_server_host:
                self._ip_edit.setText(self._state.mp_server_host)
            self._port_edit.setText(str(self._state.mp_port))
            self._lerp_chk.setChecked(self._state.mp_lerp)

        self._refresh_adv_btn()

    def _connect_signals(self):
        self._lerp_chk.toggled.connect(self._on_lerp_toggled)
        self._client.connected.connect(self._refresh_adv_btn)
        self._client.disconnected.connect(self._refresh_adv_btn)
        self._client.conn_error.connect(self._on_adv_conn_error)

    def _refresh_adv_btn(self, *_):
        if self._client.is_connected:
            self._adv_conn_btn.setText("斷線")
        else:
            self._adv_conn_btn.setText("連線")

    def _on_adv_conn_clicked(self):
        if self._client.is_connected:
            self._client.disconnect()
            return
        ip   = self._ip_edit.text().strip()
        port_txt = self._port_edit.text().strip()
        if not ip:
            self._show_adv_status("請輸入伺服器 IP")
            return
        port = int(port_txt) if port_txt.isdigit() else 9527
        # 儲存到 state
        if self._state:
            self._state.mp_server_host = ip
            self._state.mp_port        = port
        self._hide_adv_status()
        self._adv_conn_btn.setEnabled(False)
        self._client.connect_to_server(ip, port)

    def _on_adv_conn_error(self, msg: str):
        self._adv_conn_btn.setEnabled(True)
        self._show_adv_status(msg)

    def _on_lerp_toggled(self, enabled: bool):
        if self._state:
            self._state.mp_lerp = enabled
        if self._lerp_cb:
            self._lerp_cb(enabled)

    def _show_adv_status(self, msg: str):
        self._adv_status_lbl.setText(msg)
        self._adv_status_lbl.show()

    def _hide_adv_status(self):
        self._adv_status_lbl.hide()
        self._adv_status_lbl.setText("")

    def closeEvent(self, event):
        try:
            self._client.connected.disconnect(self._refresh_adv_btn)
            self._client.disconnected.disconnect(self._refresh_adv_btn)
            self._client.conn_error.disconnect(self._on_adv_conn_error)
        except Exception:
            pass
        super().closeEvent(event)


# ── 主對話框 ───────────────────────────────────────────────────────────────────

class MultiplayerDialog(QDialog):

    _DEFAULT_IP = "172.20.39.180"

    def __init__(self, network_client, state=None, lerp_changed_cb=None, parent=None):
        super().__init__(parent)
        self._client = network_client
        self._state  = state
        self._lerp_changed_cb = lerp_changed_cb  # callback(bool)
        self._in_room = False
        self._pending_rejoin_after_connect = False
        self._auto_create_on_not_found = False
        self._adv_dialog: MultiplayerAdvancedDialog | None = None

        self.setWindowTitle("多人模式")
        self.setWindowFlags(Qt.Dialog)
        self.setMinimumWidth(380)
        self._build_ui()
        self._connect_signals()
        self._refresh_connection_state()
        self._run_startup_sequence()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(12, 12, 12, 12)

        # ── 標題列 ────────────────────────────────────────────────────────────
        title_row = QHBoxLayout()
        title_lbl = QLabel("多人模式（實驗）")
        title_lbl.setStyleSheet("font-weight: bold; font-size: 14px;")
        title_row.addWidget(title_lbl)
        title_row.addStretch()

        self._status_light = QLabel()
        self._status_light.setFixedSize(14, 14)
        self._set_light("red")
        self._status_light.setToolTip("🟢 已連線　🟡 連線中　🔴 未連線")
        title_row.addWidget(self._status_light)
        root.addLayout(title_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        root.addWidget(sep)

        # ── 合作 GroupBox ─────────────────────────────────────────────────────
        coop_box = QGroupBox("合作")
        self._coop_layout = QVBoxLayout()
        self._coop_layout.setSpacing(8)
        coop_box.setLayout(self._coop_layout)
        root.addWidget(coop_box)

        self._panel_a = self._build_panel_a()
        self._coop_layout.addWidget(self._panel_a)

        self._panel_b = self._build_panel_b()
        self._coop_layout.addWidget(self._panel_b)
        self._panel_b.hide()

        # ── 競爭 GroupBox ─────────────────────────────────────────────────────
        rival_box = QGroupBox("競爭")
        rival_lay = QVBoxLayout()
        rival_lay.setContentsMargins(8, 8, 8, 8)
        coming_lbl = QLabel("🚧 構思中，敬請期待")
        coming_lbl.setAlignment(Qt.AlignCenter)
        coming_lbl.setStyleSheet("color: gray; font-style: italic;")
        rival_lay.addWidget(coming_lbl)
        rival_box.setLayout(rival_lay)
        root.addWidget(rival_box)

        # ── 底部按鈕列 ────────────────────────────────────────────────────────
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setFrameShadow(QFrame.Sunken)
        root.addWidget(sep2)

        bottom_row = QHBoxLayout()

        self._reconnect_btn = QPushButton("連線到伺服器")
        self._reconnect_btn.clicked.connect(self._on_reconnect_btn_clicked)
        bottom_row.addWidget(self._reconnect_btn, stretch=1)

        adv_btn = QPushButton("⚙  進階設定...")
        adv_btn.setFixedWidth(110)
        adv_btn.clicked.connect(self._open_advanced)
        bottom_row.addWidget(adv_btn)

        root.addLayout(bottom_row)

    def _build_panel_a(self) -> QFrame:
        frame = QFrame()
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("玩家名稱："))
        self._name_edit = QLineEdit()
        self._name_edit.setMaxLength(12)
        self._name_edit.setPlaceholderText("輸入你的名稱（中英數字）")
        name_row.addWidget(self._name_edit)
        lay.addLayout(name_row)

        room_row = QHBoxLayout()
        room_row.addWidget(QLabel("房間號碼："))
        self._room_edit = QLineEdit()
        self._room_edit.setPlaceholderText("純數字房間號碼")
        rx = QRegExp("[0-9]{1,8}")
        self._room_edit.setValidator(QRegExpValidator(rx, self._room_edit))
        room_row.addWidget(self._room_edit)
        lay.addLayout(room_row)

        btn_row = QHBoxLayout()
        self._create_btn = QPushButton("創建房間")
        self._join_btn   = QPushButton("加入房間")
        self._create_btn.clicked.connect(self._on_create_room)
        self._join_btn.clicked.connect(self._on_join_room)
        btn_row.addStretch()
        btn_row.addWidget(self._create_btn)
        btn_row.addWidget(self._join_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("color: red;")
        self._status_lbl.setWordWrap(True)
        self._status_lbl.hide()
        lay.addWidget(self._status_lbl)

        # 上次記錄區塊
        self._saved_info_frame = QFrame()
        saved_lay = QVBoxLayout(self._saved_info_frame)
        saved_lay.setContentsMargins(0, 4, 0, 0)
        saved_lay.setSpacing(4)

        self._saved_info_lbl = QLabel()
        self._saved_info_lbl.setStyleSheet("color: gray; font-size: 11px;")
        saved_lay.addWidget(self._saved_info_lbl)

        discard_row = QHBoxLayout()
        self._discard_btn = QPushButton("清除此記錄")
        self._discard_btn.clicked.connect(self._on_discard_saved)
        discard_row.addStretch()
        discard_row.addWidget(self._discard_btn)
        saved_lay.addLayout(discard_row)

        self._saved_info_frame.hide()
        lay.addWidget(self._saved_info_frame)

        # 預填
        if self._state and self._state.mp_player_name:
            self._name_edit.setText(self._state.mp_player_name)
        if self._state and self._state.mp_room_id:
            self._room_edit.setText(self._state.mp_room_id)

        return frame

    def _build_panel_b(self) -> QFrame:
        frame = QFrame()
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self._room_info_lbl = QLabel("房間 - · 0/8 人")
        self._room_info_lbl.setStyleSheet("font-weight: bold;")
        lay.addWidget(self._room_info_lbl)

        self._player_list = QListWidget()
        self._player_list.setSelectionMode(QListWidget.SingleSelection)
        self._player_list.setSortingEnabled(False)
        self._player_list.setEditTriggers(QListWidget.NoEditTriggers)
        self._player_list.itemSelectionChanged.connect(self._refresh_kick_btn)
        self._player_list.setFixedHeight(120)
        lay.addWidget(self._player_list)

        self._kick_btn = QPushButton("踢除選中玩家")
        self._kick_btn.clicked.connect(self._on_kick)
        self._kick_btn.hide()
        lay.addWidget(self._kick_btn)

        self._leave_btn = QPushButton("退出房間")
        self._leave_btn.clicked.connect(self._on_leave_room)
        lay.addWidget(self._leave_btn)

        return frame

    # ── 訊號連接 ──────────────────────────────────────────────────────────────

    def _connect_signals(self):
        c = self._client
        c.connected.connect(self._on_connected)
        c.disconnected.connect(self._on_disconnected)
        c.conn_error.connect(self._on_conn_error)
        c.room_joined.connect(self._on_room_joined)
        c.host_changed.connect(self._on_host_changed)
        c.player_joined.connect(self._on_player_joined)
        c.player_left.connect(self._on_player_left)
        c.kicked.connect(self._on_kicked)
        c.room_dissolved.connect(self._on_room_dissolved)
        c.server_error.connect(self._on_server_error)

    # ── 指示燈 ────────────────────────────────────────────────────────────────

    def _set_light(self, color: str):
        colors = {
            "green":  ("#22c55e", "#16a34a"),
            "red":    ("#ef4444", "#b91c1c"),
            "yellow": ("#eab308", "#a16207"),
        }
        fill, border = colors.get(color, colors["red"])
        self._status_light.setStyleSheet(
            f"background-color: {fill}; border: 1px solid {border}; border-radius: 7px;"
        )

    # ── UI 狀態工具 ───────────────────────────────────────────────────────────

    def _refresh_connection_state(self):
        connected = self._client.is_connected
        self._create_btn.setEnabled(connected)
        self._join_btn.setEnabled(connected)
        if connected:
            self._set_light("green")
            self._reconnect_btn.setText("斷線")
            if self._client.current_room and not self._in_room:
                self._in_room = True
                self._panel_b.show()
                self._populate_player_list()
        else:
            self._set_light("red")
            self._reconnect_btn.setText("連線到伺服器")

    def _show_status(self, msg: str, color: str = "red"):
        self._status_lbl.setStyleSheet(f"color: {color};")
        self._status_lbl.setText(msg)
        self._status_lbl.show()

    def _hide_status(self):
        self._status_lbl.hide()
        self._status_lbl.setText("")

    def _switch_to_panel_a(self):
        self._in_room = False
        self._panel_b.hide()
        self._panel_a.show()
        self._update_saved_info_ui()

    def _switch_to_panel_b(self):
        self._in_room = True
        self._panel_a.show()
        self._hide_status()
        self._panel_b.show()

    def _populate_player_list(self):
        self._player_list.clear()
        my_name = self._client.player_name or ""
        host    = self._client.room_host or ""
        for p in self._client.room_players:
            suffix = ""
            if p == host:
                suffix += "（房主）"
            if p == my_name:
                suffix += "（你）✓"
            item = QListWidgetItem(f"  {p}{suffix}")
            self._player_list.addItem(item)
        count = len(self._client.room_players)
        room  = self._client.current_room or "-"
        self._room_info_lbl.setText(f"房間 {room} · {count}/8 人")
        self._refresh_kick_btn()

    def _refresh_kick_btn(self):
        my_name = self._client.player_name or ""
        host    = self._client.room_host or ""
        is_host = (my_name == host)
        if is_host:
            self._kick_btn.show()
            item = self._player_list.currentItem()
            if item and "（你）" in item.text():
                self._kick_btn.setEnabled(False)
            else:
                self._kick_btn.setEnabled(item is not None)
        else:
            self._kick_btn.hide()

    # ── 智能啟動流程 ──────────────────────────────────────────────────────────

    def _run_startup_sequence(self):
        if self._client.is_connected:
            if not self._client.current_room:
                self._try_rejoin_or_create()
        elif self._client.is_connecting:
            self._set_light("yellow")
            self._reconnect_btn.setEnabled(False)
        else:
            target = (self._state.mp_server_host
                      if self._state and self._state.mp_server_host
                      else self._DEFAULT_IP)
            port = (self._state.mp_port
                    if self._state else 9527)
            self._pending_rejoin_after_connect = True
            self._set_light("yellow")
            self._reconnect_btn.setEnabled(False)
            self._client.connect_to_server(target, port)

    def _try_rejoin_or_create(self):
        if not self._state or not self._state.mp_room_id:
            return
        self._auto_create_on_not_found = True
        self._client.set_name(self._state.mp_player_name)
        self._client.join_room(self._state.mp_room_id)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_reconnect_btn_clicked(self):
        """主對話框的連線/斷線按鈕：使用 state 中儲存的 IP/port。"""
        if self._client.is_connected:
            self._set_light("yellow")
            # 主動斷線前先離開房間，讓 room_left 信號把 widget.py 的
            # _was_in_room 清成 False，避免誤觸自動重連
            if self._client.current_room:
                self._client.leave_room()
            self._client.disconnect()
        else:
            ip   = (self._state.mp_server_host
                    if self._state and self._state.mp_server_host
                    else self._DEFAULT_IP)
            port = (self._state.mp_port if self._state else 9527)
            self._set_light("yellow")
            self._reconnect_btn.setEnabled(False)
            self._client.connect_to_server(ip, port)

    def _open_advanced(self):
        if self._adv_dialog is not None and self._adv_dialog.isVisible():
            self._adv_dialog.raise_()
            self._adv_dialog.activateWindow()
            return
        self._adv_dialog = MultiplayerAdvancedDialog(
            self._client, self._state,
            lerp_changed_cb=self._lerp_changed_cb,
            parent=self,
        )
        self._adv_dialog.show()

    def _on_create_room(self):
        name = self._name_edit.text().strip()
        room = self._room_edit.text().strip()
        if not name:
            self._show_status("請輸入玩家名稱")
            return
        if not room:
            self._show_status("請輸入房間號碼")
            return
        self._hide_status()
        self._client.set_name(name)
        self._client.create_room(room)

    def _on_join_room(self):
        name = self._name_edit.text().strip()
        room = self._room_edit.text().strip()
        if not name:
            self._show_status("請輸入玩家名稱")
            return
        if not room:
            self._show_status("請輸入房間號碼")
            return
        self._hide_status()
        self._client.set_name(name)
        self._client.join_room(room)

    def _on_kick(self):
        item = self._player_list.currentItem()
        if not item:
            return
        raw  = item.text().strip()
        name = raw.split("（")[0].strip()
        self._client.kick(name)

    def _on_leave_room(self):
        self._client.leave_room()
        if self._state:
            self._state.mp_room_id     = ""
            self._state.mp_player_name = ""
            self._state.mp_server_host = ""
        self._switch_to_panel_a()

    # ── Signal handlers ───────────────────────────────────────────────────────

    def _on_connected(self):
        self._set_light("green")
        self._reconnect_btn.setText("斷線")
        self._reconnect_btn.setEnabled(True)
        self._create_btn.setEnabled(True)
        self._join_btn.setEnabled(True)
        if self._pending_rejoin_after_connect:
            self._pending_rejoin_after_connect = False
            self._try_rejoin_or_create()

    def _on_disconnected(self, reason: str):
        self._set_light("red")
        self._reconnect_btn.setText("連線到伺服器")
        self._reconnect_btn.setEnabled(True)
        self._create_btn.setEnabled(False)
        self._join_btn.setEnabled(False)
        if self._in_room:
            self._switch_to_panel_a()
        if reason:
            self._show_status(reason)

    def _on_conn_error(self, msg: str):
        self._set_light("red")
        self._reconnect_btn.setText("連線到伺服器")
        self._reconnect_btn.setEnabled(True)
        self._show_status(msg)

    def _on_room_joined(self, room_id: str, players: list, host: str):
        if self._state and self._client.player_name:
            my_name = self._client.player_name
            self._state.mp_room_id     = room_id
            self._state.mp_player_name = my_name
            self._state.mp_server_host = self._client.server_host
        self._auto_create_on_not_found = False
        self._switch_to_panel_b()
        self._populate_player_list()

    def _on_host_changed(self, new_host: str):
        if self._in_room:
            self._populate_player_list()

    def _on_player_joined(self, name: str):
        self._populate_player_list()

    def _on_player_left(self, name: str):
        self._populate_player_list()

    def _on_kicked(self):
        if self._state:
            self._state.mp_room_id     = ""
            self._state.mp_player_name = ""
            self._state.mp_server_host = ""
        self._switch_to_panel_a()
        self._show_status("你已被踢出房間")

    def _on_room_dissolved(self):
        self._switch_to_panel_a()
        self._show_status("房間已被解散")

    def _on_server_error(self, code: str, msg: str):
        if (code == "ROOM_NOT_FOUND"
                and self._auto_create_on_not_found
                and self._state is not None
                and self._state.mp_room_id
                and self._client.is_connected
                and not self._client.current_room):
            self._auto_create_on_not_found = False
            self._client.create_room(self._state.mp_room_id)
        else:
            self._auto_create_on_not_found = False
            self._show_status(msg)

    def _update_saved_info_ui(self):
        if self._state is None or not self._state.mp_room_id:
            self._saved_info_frame.hide()
            return
        if self._in_room:
            self._saved_info_frame.hide()
            return
        self._saved_info_lbl.setText(
            f"📌 上次記錄：房間 {self._state.mp_room_id}"
            f"  |  名稱：{self._state.mp_player_name}"
        )
        self._saved_info_frame.show()

    def _on_discard_saved(self):
        if self._state:
            self._state.mp_room_id     = ""
            self._state.mp_player_name = ""
            self._state.mp_server_host = ""
        self._name_edit.clear()
        self._room_edit.clear()
        self._update_saved_info_ui()
        self._hide_status()

    # ── 對話框關閉 ────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        self._disconnect_signals()
        if self._adv_dialog is not None:
            self._adv_dialog.close()
        super().closeEvent(event)

    def _disconnect_signals(self):
        c = self._client
        for sig, slot in [
            (c.connected,      self._on_connected),
            (c.disconnected,   self._on_disconnected),
            (c.conn_error,     self._on_conn_error),
            (c.room_joined,    self._on_room_joined),
            (c.host_changed,   self._on_host_changed),
            (c.player_joined,  self._on_player_joined),
            (c.player_left,    self._on_player_left),
            (c.kicked,         self._on_kicked),
            (c.room_dissolved, self._on_room_dissolved),
            (c.server_error,   self._on_server_error),
        ]:
            try:
                sig.disconnect(slot)
            except Exception:
                pass
