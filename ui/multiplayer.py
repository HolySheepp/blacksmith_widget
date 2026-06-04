"""
MultiplayerDialog — 多人模式非模態對話框。
依循 devtools.py 的風格：QVBoxLayout, QGroupBox, QFormLayout, 間距 10px, 邊距 12px。
"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QGroupBox, QListWidget, QListWidgetItem,
    QFrame, QSizePolicy, QWidget,
)
from PyQt5.QtGui import QRegExpValidator
from PyQt5.QtCore import QRegExp


class MultiplayerDialog(QDialog):

    _DEFAULT_IP = "172.20.39.180"

    def __init__(self, network_client, state=None, parent=None):
        super().__init__(parent)
        self._client = network_client
        self._state  = state          # GameState，用於讀寫 mp_* 欄位
        self._in_room = False
        self._pending_rejoin_after_connect = False  # 連線成功後執行重加入
        self._auto_create_on_not_found = False      # join 失敗時自動 create

        self.setWindowTitle("多人模式")
        self.setWindowFlags(Qt.Dialog)          # 非模態（show() 呼叫者負責）
        self.setMinimumWidth(380)
        self._build_ui()
        self._connect_signals()
        self._refresh_connection_state()
        # 開啟時自動執行智能啟動流程（連線 → 加入/創建房間）
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

        # ── 預先建立 IP 欄位（_build_panel_a 需要填入預設值）────────────────
        self._ip_edit = QLineEdit()
        self._ip_edit.setPlaceholderText(self._DEFAULT_IP)
        self._ip_edit.setFixedWidth(130)

        self._conn_btn = QPushButton("連線")
        self._conn_btn.setFixedWidth(54)
        self._conn_btn.clicked.connect(self._on_conn_btn_clicked)

        # ── 合作 GroupBox ─────────────────────────────────────────────────────
        coop_box = QGroupBox("合作")
        self._coop_layout = QVBoxLayout()
        self._coop_layout.setSpacing(8)
        coop_box.setLayout(self._coop_layout)
        root.addWidget(coop_box)

        # 狀態 A（不在房間）
        self._panel_a = self._build_panel_a()
        self._coop_layout.addWidget(self._panel_a)

        # 狀態 B（在房間中）
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

        # ── 連線區（移至最下方，一般情況不需改動）──────────────────────────
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setFrameShadow(QFrame.Sunken)
        root.addWidget(sep2)

        conn_row = QHBoxLayout()
        conn_lbl = QLabel("伺服器 IP（一般情況不用改動）：")
        conn_lbl.setStyleSheet("color: gray; font-size: 11px;")
        conn_row.addWidget(conn_lbl)
        conn_row.addWidget(self._ip_edit)
        conn_row.addWidget(self._conn_btn)
        root.addLayout(conn_row)

    def _build_panel_a(self) -> QFrame:
        frame = QFrame()
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        # 玩家名稱
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("玩家名稱："))
        self._name_edit = QLineEdit()
        self._name_edit.setMaxLength(12)
        self._name_edit.setPlaceholderText("輸入你的名稱（中英數字）")
        name_row.addWidget(self._name_edit)
        lay.addLayout(name_row)

        # 房間號碼
        room_row = QHBoxLayout()
        room_row.addWidget(QLabel("房間號碼："))
        self._room_edit = QLineEdit()
        self._room_edit.setPlaceholderText("純數字房間號碼")
        rx = QRegExp("[0-9]{1,8}")
        self._room_edit.setValidator(QRegExpValidator(rx, self._room_edit))
        room_row.addWidget(self._room_edit)
        lay.addLayout(room_row)

        # 按鈕列
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

        # 狀態文字（錯誤訊息）
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("color: red;")
        self._status_lbl.setWordWrap(True)
        self._status_lbl.hide()
        lay.addWidget(self._status_lbl)

        # ── 上次記錄 ─────────────────────────────────────────────────────────────
        # 顯示條件：有 state.mp_room_id 且不在房間中
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

        # 預設隱藏整個 frame，之後由 _update_saved_info_ui 控制
        self._saved_info_frame.hide()
        lay.addWidget(self._saved_info_frame)

        # 若有上次記錄，預先填入欄位
        if self._state and self._state.mp_player_name:
            self._name_edit.setText(self._state.mp_player_name)
        if self._state and self._state.mp_room_id:
            self._room_edit.setText(self._state.mp_room_id)
        # IP：優先用記錄，其次用預設 IP
        if self._state and self._state.mp_server_host:
            self._ip_edit.setText(self._state.mp_server_host)
        else:
            self._ip_edit.setText(self._DEFAULT_IP)

        return frame

    def _build_panel_b(self) -> QFrame:
        frame = QFrame()
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        # 房間資訊列
        self._room_info_lbl = QLabel("房間 - · 0/8 人")
        self._room_info_lbl.setStyleSheet("font-weight: bold;")
        lay.addWidget(self._room_info_lbl)

        # 玩家列表
        self._player_list = QListWidget()
        self._player_list.setSelectionMode(QListWidget.SingleSelection)
        self._player_list.setSortingEnabled(False)
        self._player_list.setEditTriggers(QListWidget.NoEditTriggers)
        self._player_list.itemSelectionChanged.connect(self._refresh_kick_btn)
        self._player_list.setFixedHeight(120)
        lay.addWidget(self._player_list)

        # 踢除按鈕
        self._kick_btn = QPushButton("踢除選中玩家")
        self._kick_btn.clicked.connect(self._on_kick)
        self._kick_btn.hide()
        lay.addWidget(self._kick_btn)

        # 退出房間
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
        """color: 'green' | 'red' | 'yellow'"""
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
            self._conn_btn.setText("斷線")
            # 若已在房間中（重新開啟對話框），恢復 panel B
            if self._client.current_room and not self._in_room:
                self._in_room = True
                self._panel_b.show()
                self._populate_player_list()
        else:
            self._set_light("red")
            self._conn_btn.setText("連線")

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
        self._update_saved_info_ui()   # 更新上次記錄區塊

    def _switch_to_panel_b(self):
        self._in_room = True
        self._panel_a.show()   # panel_a 的 status_lbl 仍隱藏
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

    # ── Slots ─────────────────────────────────────────────────────────────────

    # ── 智能啟動流程 ──────────────────────────────────────────────────────────

    def _run_startup_sequence(self):
        """對話框開啟時自動判斷連線/房間狀態並採取對應動作。

        三種情況：
        A. 已連線且在房間  → _refresh_connection_state 已處理，什麼都不做
        B. 已連線但不在房間 → 嘗試重新加入/創建（由本 dialog 主導）
        C. 連線中（auto-rejoin thread 執行中）→ 顯示黃燈等待，不重複嘗試連線
           widget.py 的 _do_auto_rejoin 會處理房間加入，room_joined 信號觸發 Panel B
        D. 未連線          → 啟動新連線，連線成功後由本 dialog 加入房間
        """
        if self._client.is_connected:
            if not self._client.current_room:
                # B: 已連線但不在房間
                self._try_rejoin_or_create()
            # else: A 已處理
        elif self._client.is_connecting:
            # C: 已有連線進行中（widget auto-rejoin），等待即可
            self._set_light("yellow")
            self._conn_btn.setEnabled(False)
            # _pending_rejoin_after_connect 保持 False：
            # 讓 widget._do_auto_rejoin 主導房間加入，dialog 接收 room_joined 訊號
        else:
            # D: 未連線，由本 dialog 啟動連線
            target = (self._state.mp_server_host
                      if self._state and self._state.mp_server_host
                      else self._DEFAULT_IP)
            self._ip_edit.setText(target)
            self._pending_rejoin_after_connect = True
            self._set_light("yellow")
            self._conn_btn.setEnabled(False)
            self._client.connect_to_server(target)

    def _try_rejoin_or_create(self):
        """已連線但不在房間：嘗試加入，若房間不存在則自動創建。"""
        if not self._state or not self._state.mp_room_id:
            return  # 沒有上次記錄，不自動動作
        self._auto_create_on_not_found = True
        self._client.set_name(self._state.mp_player_name)
        self._client.join_room(self._state.mp_room_id)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_conn_btn_clicked(self):
        if self._client.is_connected:
            self._set_light("yellow")
            self._client.disconnect()
        else:
            ip = self._ip_edit.text().strip()
            if not ip:
                self._show_status("請輸入伺服器 IP")
                return
            self._set_light("yellow")
            self._conn_btn.setEnabled(False)
            self._client.connect_to_server(ip)

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
        # 從顯示文字取出名稱（去除前置空格與後綴括號內容）
        raw = item.text().strip()
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
        self._conn_btn.setText("斷線")
        self._conn_btn.setEnabled(True)
        self._create_btn.setEnabled(True)
        self._join_btn.setEnabled(True)
        # 啟動序列觸發的連線 → 繼續嘗試加入/創建房間
        if self._pending_rejoin_after_connect:
            self._pending_rejoin_after_connect = False
            self._try_rejoin_or_create()

    def _on_disconnected(self, reason: str):
        self._set_light("red")
        self._conn_btn.setText("連線")
        self._conn_btn.setEnabled(True)
        self._create_btn.setEnabled(False)
        self._join_btn.setEnabled(False)
        if self._in_room:
            self._switch_to_panel_a()
        if reason:
            self._show_status(reason)

    def _on_conn_error(self, msg: str):
        self._set_light("red")
        self._conn_btn.setText("連線")
        self._conn_btn.setEnabled(True)
        self._show_status(msg)

    def _on_room_joined(self, room_id: str, players: list, host: str):
        # 更新 state 的記錄（以防 multiplayer.py 是加入流程的發起者）
        if self._state and self._client.player_name:
            my_name = self._client.player_name
            self._state.mp_room_id     = room_id
            self._state.mp_player_name = my_name
            self._state.mp_server_host = self._client.server_host
        self._auto_create_on_not_found = False
        self._switch_to_panel_b()
        self._populate_player_list()

    def _on_host_changed(self, new_host: str):
        """房主轉移時更新玩家列表顯示。"""
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
        # 管理員強制解散房間時觸發
        self._switch_to_panel_a()
        self._show_status("房間已被解散")

    def _on_server_error(self, code: str, msg: str):
        if (code == "ROOM_NOT_FOUND"
                and self._auto_create_on_not_found
                and self._state is not None
                and self._state.mp_room_id
                and self._client.is_connected
                and not self._client.current_room):
            # 自動重連時房間不存在 → 自動創建（成為新房主）
            self._auto_create_on_not_found = False
            self._client.create_room(self._state.mp_room_id)
        else:
            self._auto_create_on_not_found = False
            self._show_status(msg)

    def _update_saved_info_ui(self):
        """根據 state.mp_* 更新上次記錄區塊的顯示。"""
        if self._state is None or not self._state.mp_room_id:
            self._saved_info_frame.hide()
            return
        if self._in_room:
            self._saved_info_frame.hide()
            return
        # 顯示記錄摘要 + 放棄按鈕
        self._saved_info_lbl.setText(
            f"📌 上次記錄：房間 {self._state.mp_room_id}"
            f"  |  名稱：{self._state.mp_player_name}"
        )
        self._saved_info_frame.show()

    def _on_discard_saved(self):
        """放棄上次記錄，清除欄位。"""
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
        """關閉時斷開 signal 連接，避免下次開啟時重複接收。"""
        self._disconnect_signals()
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
