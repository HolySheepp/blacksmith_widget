# -*- coding: utf-8 -*-
"""
鐵砧伺服器 v1.0
帶有 PyQt5 GUI 的 WebSocket 放置遊戲伺服器。

架構：
    PyQt5 主視窗（Qt 主執行緒）
    + asyncio WebSocket 伺服器（daemon 執行緒）

    asyncio 執行緒 -> 透過 Qt signals 安全更新 GUI
    Qt 執行緒     -> 透過 asyncio.run_coroutine_threadsafe() 呼叫 async 函式
"""

import sys
import os
import time
import json
import asyncio
import threading
from dataclasses import dataclass, field

try:
    import websockets
except ImportError:
    websockets = None

from PyQt5.QtCore import Qt, QObject, pyqtSignal, QTimer
from PyQt5.QtGui import QIntValidator
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QListWidgetItem, QGroupBox, QLabel, QGridLayout,
    QSplitter, QAction, QMessageBox, QLineEdit, QPushButton,
    QDialog, QDialogButtonBox, QCheckBox,
)

# ---------------------------------------------------------------------------
# 常數
# ---------------------------------------------------------------------------

HOST = "0.0.0.0"
PORT = 9527
MAX_ROOM_SIZE = 8
MAX_NAME_LEN = 12
PING_INTERVAL = 30.0      # 每 30 秒傳 ping
PONG_TIMEOUT = 10.0       # 10 秒內未收到 pong 則斷線
SERVER_VERSION = "1.0"

SCRIPT_PATH = os.path.abspath(__file__)
STARTUP_BAT = os.path.join(
    os.environ.get("APPDATA", ""),
    r"Microsoft\Windows\Start Menu\Programs\Startup",
    "鐵砧伺服器.bat",
)

# 非法名稱字元（控制字元）
_ILLEGAL_NAME_CHARS = set(chr(c) for c in range(0x20))


# ---------------------------------------------------------------------------
# 資料結構
# ---------------------------------------------------------------------------

@dataclass
class ClientInfo:
    ws: object                       # websockets WebSocketServerProtocol
    ip: str
    connected_since: float           # time.time()
    name: "str | None" = None
    room: "str | None" = None
    hit_count: int = 0
    click_count: int = 0
    force_count: int = 0
    play_time: float = 0.0
    forge_counts: list = field(default_factory=lambda: [0] * 5)
    charge: float = 0.0
    awaiting_pong: bool = False      # 已送出 ping 等待 pong 中


# 全域（僅在 asyncio loop 內存取，不需鎖）
clients: "dict[str, ClientInfo]" = {}    # client_id -> ClientInfo
rooms: "dict[str, list[str]]" = {}       # room_id -> [client_id, ...]
room_hosts: "dict[str, str]" = {}        # room_id -> host_client_id


# ---------------------------------------------------------------------------
# Qt 訊號橋接：從 asyncio 執行緒安全更新 GUI
# ---------------------------------------------------------------------------

class _Bridge(QObject):
    """所有從伺服器執行緒發往 GUI 的訊號集中於此。"""
    state_changed = pyqtSignal()       # 玩家 / 房間清單有變動
    server_started = pyqtSignal()
    server_error = pyqtSignal(str)
    server_stopped = pyqtSignal()


bridge = _Bridge()


def _notify_state():
    """從 asyncio 執行緒呼叫，通知 GUI 重繪清單。"""
    bridge.state_changed.emit()


# ---------------------------------------------------------------------------
# WebSocket 協定輔助函式
# ---------------------------------------------------------------------------

async def _send(ws, obj):
    """送出 JSON 訊息，忽略連線已關閉的錯誤。"""
    try:
        await ws.send(json.dumps(obj, ensure_ascii=False))
    except Exception:
        pass


async def _send_error(ws, code, msg):
    await _send(ws, {"type": "error", "code": code, "msg": msg})


async def _send_ok(ws):
    await _send(ws, {"type": "ok"})


def _name_taken(name, exclude_cid=None):
    for cid, info in clients.items():
        if cid == exclude_cid:
            continue
        if info.name == name:
            return True
    return False


def _name_taken_in_room(name, room, exclude_cid=None):
    for cid in rooms.get(room, []):
        if cid == exclude_cid:
            continue
        info = clients.get(cid)
        if info and info.name == name:
            return True
    return False


def _validate_name(name):
    if not isinstance(name, str):
        return False
    s = name.strip()
    if not s:
        return False
    if len(s) > MAX_NAME_LEN:
        return False
    if any(c in _ILLEGAL_NAME_CHARS for c in s):
        return False
    return True


def _room_state_payload(room):
    players = []
    for cid in rooms.get(room, []):
        info = clients.get(cid)
        if info and info.name:
            players.append(info.name)
        elif info:
            players.append("未命名")
    host_cid = room_hosts.get(room)
    host_info = clients.get(host_cid) if host_cid else None
    host_name = host_info.name if host_info and host_info.name else ""
    return {
        "type": "room_state",
        "room": room,
        "players": players,
        "host": host_name,
    }


async def _broadcast_room(room, obj, exclude_cid=None):
    """廣播給房間內所有人（可排除某人）。"""
    for cid in list(rooms.get(room, [])):
        if cid == exclude_cid:
            continue
        info = clients.get(cid)
        if info:
            await _send(info.ws, obj)


async def _remove_from_room(cid, *, broadcast=True):
    """
    將客戶端自其所在房間移除。
    - 一律先移出成員列表並廣播 player_left。
    - 若該客戶端為房主且房間仍有其他人 -> 轉移房主給下一位成員並廣播 host_changed。
    - 若房間已空 -> 刪除房間。
    """
    info = clients.get(cid)
    if not info or not info.room:
        return
    room = info.room
    info.room = None

    # 從成員列表移除
    if room in rooms and cid in rooms[room]:
        rooms[room].remove(cid)

    # 廣播離開
    if broadcast and info.name:
        await _broadcast_room(room, {"type": "player_left", "name": info.name})

    # 房間已空 → 清除
    if not rooms.get(room):
        rooms.pop(room, None)
        room_hosts.pop(room, None)
        _notify_state()
        return

    # 若離開者為房主 → 轉移房主給下一位成員
    if room_hosts.get(room) == cid:
        next_cid = rooms[room][0]
        room_hosts[room] = next_cid
        next_info = clients.get(next_cid)
        new_host = next_info.name if next_info and next_info.name else ""
        if broadcast:
            await _broadcast_room(room, {"type": "host_changed", "host": new_host})

    _notify_state()


# ---------------------------------------------------------------------------
# 訊息處理
# ---------------------------------------------------------------------------

async def _handle_message(cid, raw):
    info = clients.get(cid)
    if not info:
        return
    ws = info.ws

    try:
        msg = json.loads(raw)
    except Exception:
        await _send_error(ws, "INVALID_NAME", "訊息格式錯誤")
        return
    if not isinstance(msg, dict):
        return

    mtype = msg.get("type")

    # --- set_name ---------------------------------------------------------
    if mtype == "set_name":
        name = msg.get("name")
        if not _validate_name(name):
            await _send_error(ws, "INVALID_NAME", "名稱空白、超過12字或含非法字元")
            return
        name = name.strip()
        info.name = name
        await _send_ok(ws)
        _notify_state()

    # --- create_room ------------------------------------------------------
    elif mtype == "create_room":
        room = msg.get("room")
        if not info.name:
            await _send_error(ws, "NO_NAME", "請先設定名稱")
            return
        if info.room:
            await _send_error(ws, "ALREADY_IN_ROOM", "你已在房間中")
            return
        if not isinstance(room, str) or not room.strip():
            await _send_error(ws, "ROOM_NOT_FOUND", "房間號碼無效")
            return
        room = room.strip()
        if room in rooms:
            await _send_error(ws, "ROOM_EXISTS", "房間已存在")
            return
        rooms[room] = [cid]
        room_hosts[room] = cid
        info.room = room
        await _send(ws, _room_state_payload(room))
        _notify_state()

    # --- join_room --------------------------------------------------------
    elif mtype == "join_room":
        room = msg.get("room")
        if not info.name:
            await _send_error(ws, "NO_NAME", "請先設定名稱")
            return
        if info.room:
            await _send_error(ws, "ALREADY_IN_ROOM", "你已在房間中")
            return
        if not isinstance(room, str) or room.strip() not in rooms:
            await _send_error(ws, "ROOM_NOT_FOUND", "房間不存在")
            return
        room = room.strip()
        if len(rooms[room]) >= MAX_ROOM_SIZE:
            await _send_error(ws, "ROOM_FULL", "房間已滿（最多8人）")
            return
        if _name_taken_in_room(info.name, room, exclude_cid=cid):
            await _send_error(ws, "NAME_TAKEN", "房間內已有相同名稱")
            return
        rooms[room].append(cid)
        info.room = room
        # 先廣播給已在房間者，再回傳完整狀態給加入者
        await _broadcast_room(room, {"type": "player_joined", "name": info.name},
                              exclude_cid=cid)
        await _send(ws, _room_state_payload(room))
        _notify_state()

    # --- leave_room -------------------------------------------------------
    elif mtype == "leave_room":
        if not info.room:
            await _send_error(ws, "ROOM_NOT_FOUND", "你不在任何房間")
            return
        await _remove_from_room(cid, broadcast=True)
        await _send_ok(ws)
        _notify_state()

    # --- kick -------------------------------------------------------------
    elif mtype == "kick":
        target_name = msg.get("target")
        room = info.room
        if not room:
            await _send_error(ws, "ROOM_NOT_FOUND", "你不在任何房間")
            return
        if room_hosts.get(room) != cid:
            await _send_error(ws, "NOT_HOST", "只有房主能踢人")
            return
        # 找出目標 client_id
        target_cid = None
        for member_cid in rooms.get(room, []):
            m = clients.get(member_cid)
            if m and m.name == target_name and member_cid != cid:
                target_cid = member_cid
                break
        if target_cid is None:
            await _send_error(ws, "PLAYER_NOT_FOUND", "找不到該玩家")
            return
        target = clients.get(target_cid)
        # 通知被踢者
        await _send(target.ws, {"type": "kicked"})
        # 自房間移除並廣播 player_left
        if target_cid in rooms.get(room, []):
            rooms[room].remove(target_cid)
        target.room = None
        if target.name:
            await _broadcast_room(room, {"type": "player_left", "name": target.name})
        await _send_ok(ws)
        _notify_state()

    # --- frame（轉播 + 統計）---------------------------------------------
    elif mtype == "frame":
        data = msg.get("data")
        if not isinstance(data, dict):
            return
        # 更新統計
        if isinstance(data.get("hit_count"), int):
            info.hit_count = data["hit_count"]
        if isinstance(data.get("click_count"), int):
            info.click_count = data["click_count"]
        if isinstance(data.get("force_count"), int):
            info.force_count = data["force_count"]
        if isinstance(data.get("play_time"), (int, float)):
            info.play_time = float(data["play_time"])
        fc = data.get("forge_counts")
        if isinstance(fc, list):
            for i, v in enumerate(fc):
                if i < len(info.forge_counts) and isinstance(v, int):
                    info.forge_counts[i] = v
        chg = data.get("charge")
        if isinstance(chg, (int, float)):
            info.charge = float(chg)
        # 轉播給同房間其他人
        if info.room:
            await _broadcast_room(
                info.room,
                {"type": "frame", "from": info.name, "data": data},
                exclude_cid=cid,
            )

    # --- chat -------------------------------------------------------------
    elif mtype == "chat":
        text = msg.get("text")
        if not isinstance(text, str):
            return
        if info.room:
            await _broadcast_room(
                info.room,
                {"type": "chat", "from": info.name, "text": text},
            )

    # --- pong -------------------------------------------------------------
    elif mtype == "pong":
        info.awaiting_pong = False

    # 未知訊息類型 -> 忽略


# ---------------------------------------------------------------------------
# 連線處理
# ---------------------------------------------------------------------------

async def _keepalive(cid):
    """每 PING_INTERVAL 秒送出 ping；PONG_TIMEOUT 內未回 pong 則斷線。"""
    try:
        while cid in clients:
            await asyncio.sleep(PING_INTERVAL)
            info = clients.get(cid)
            if not info:
                return
            info.awaiting_pong = True
            await _send(info.ws, {"type": "ping"})
            await asyncio.sleep(PONG_TIMEOUT)
            info = clients.get(cid)
            if not info:
                return
            if info.awaiting_pong:
                # 超時未回應 -> 關閉連線
                try:
                    await info.ws.close()
                except Exception:
                    pass
                return
    except asyncio.CancelledError:
        return


async def _handler(ws, *args):
    """每個 WebSocket 連線一個 handler。"""
    # 相容不同 websockets 版本（10.x handler 第二參數為 path）
    peer = getattr(ws, "remote_address", None)
    if peer and isinstance(peer, (tuple, list)) and len(peer) >= 2:
        ip = "{}:{}".format(peer[0], peer[1])
    else:
        ip = "未知"

    cid = "{}#{}".format(id(ws), time.time())
    clients[cid] = ClientInfo(ws=ws, ip=ip, connected_since=time.time())
    _notify_state()

    # 連線時送出 server_info
    await _send(ws, {"type": "server_info", "version": SERVER_VERSION})

    ka_task = asyncio.ensure_future(_keepalive(cid))

    try:
        async for raw in ws:
            await _handle_message(cid, raw)
    except Exception:
        pass
    finally:
        ka_task.cancel()
        # 斷線處理：移出房間（房主則解散）後刪除
        await _remove_from_room(cid, broadcast=True)
        clients.pop(cid, None)
        _notify_state()


# ---------------------------------------------------------------------------
# 伺服器執行緒
# ---------------------------------------------------------------------------

class _ServerThread(threading.Thread):
    def __init__(self, port):
        super().__init__(daemon=True)
        self.port = port
        self.loop = asyncio.new_event_loop()
        self._stop_fut = None

    def run(self):
        asyncio.set_event_loop(self.loop)
        try:
            self._stop_fut = self.loop.create_future()
            self.loop.run_until_complete(self._serve())
        except Exception as e:
            bridge.server_error.emit(str(e))

    async def _serve(self):
        if websockets is None:
            bridge.server_error.emit("未安裝 websockets 套件")
            return
        async with websockets.serve(self._handler, HOST, self.port):
            bridge.server_started.emit()
            await self._stop_fut  # run until stop() is called
        bridge.server_stopped.emit()

    async def _handler(self, ws, *args):
        await _handler(ws, *args)

    def schedule(self, coro):
        """從 Qt 執行緒呼叫 async 函式。"""
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def stop(self):
        """從任意執行緒安全停止伺服器。"""
        if self._stop_fut is not None and not self._stop_fut.done():
            self.loop.call_soon_threadsafe(self._stop_fut.set_result, None)


# ---------------------------------------------------------------------------
# 開機自啟（Windows Startup 資料夾 .bat）
# ---------------------------------------------------------------------------

def startup_enabled():
    return os.path.isfile(STARTUP_BAT)


def enable_startup():
    content = 'start /B pythonw "{}"\n'.format(SCRIPT_PATH)
    os.makedirs(os.path.dirname(STARTUP_BAT), exist_ok=True)
    with open(STARTUP_BAT, "w", encoding="utf-8") as f:
        f.write(content)


def disable_startup():
    try:
        if os.path.isfile(STARTUP_BAT):
            os.remove(STARTUP_BAT)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 主視窗
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self._port = PORT
        self._server = None

        self.setWindowTitle("鐵砧伺服器 v1.0")
        self.resize(820, 600)

        self._selected_cid = None     # 目前選中的玩家 client_id

        self._build_ui()
        self._build_menu()

        # 訊號連接
        bridge.state_changed.connect(self._refresh_lists)
        bridge.server_started.connect(self._on_server_started)
        bridge.server_error.connect(self._on_server_error)
        bridge.server_stopped.connect(self._on_server_stopped)

        # 每 1 秒刷新右側詳情與狀態列
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_detail)
        self._timer.timeout.connect(self._refresh_status)
        self._timer.start(1000)

        self._refresh_lists()
        self._refresh_status()

        # 自動啟動伺服器
        self._start_server()

    # ---- 伺服器管理 -------------------------------------------------------
    def _start_server(self):
        global rooms, clients, room_hosts
        rooms.clear()
        clients.clear()
        room_hosts.clear()
        self._server = _ServerThread(self._port)
        self._server.start()
        if hasattr(self, '_act_start'):
            self._act_start.setEnabled(False)
            self._act_stop.setEnabled(True)
        self.statusBar().showMessage(f"正在啟動… port {self._port}")

    def _stop_server(self):
        if self._server:
            self._server.stop()

    # ---- UI 建構 --------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)

        # 上半部：左玩家清單 + 右詳情
        top_split = QSplitter(Qt.Horizontal)

        # 左側：已連線玩家
        left_box = QGroupBox("已連線玩家")
        left_layout = QVBoxLayout(left_box)
        self.player_list = QListWidget()
        self.player_list.setMinimumWidth(200)
        self.player_list.setMaximumWidth(260)
        self.player_list.itemClicked.connect(self._on_player_clicked)
        left_layout.addWidget(self.player_list)
        top_split.addWidget(left_box)

        # 右側：玩家詳情
        detail_box = QGroupBox("玩家詳情")
        grid = QGridLayout(detail_box)
        self._detail_labels = {}
        _METAL_NAMES = ["破銅", "爛鐵", "鐵", "鋼", "精金"]
        fields = [
            ("name",     "名稱："),
            ("ip",       "IP："),
            ("time",     "連線時間："),
            ("room",     "目前房間："),
            ("playtime", "遊玩時長："),
            ("hit",      "打擊計數："),
            ("force",    "力道計數："),
            ("click",    "點擊計數："),
        ] + [(f"forge_{i}", f"鍛造 {n}：") for i, n in enumerate(_METAL_NAMES)]
        for i, (key, label) in enumerate(fields):
            grid.addWidget(QLabel(label), i, 0, alignment=Qt.AlignRight | Qt.AlignTop)
            val = QLabel("—")
            self._detail_labels[key] = val
            grid.addWidget(val, i, 1, alignment=Qt.AlignLeft | Qt.AlignTop)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(len(fields), 1)
        top_split.addWidget(detail_box)
        top_split.setStretchFactor(0, 0)
        top_split.setStretchFactor(1, 1)

        outer.addWidget(top_split, stretch=3)

        # 下半部：房間列表
        room_box = QGroupBox("房間列表")
        room_layout = QVBoxLayout(room_box)
        self.room_list = QListWidget()
        self.room_list.itemDoubleClicked.connect(self._on_room_double_clicked)
        room_layout.addWidget(self.room_list)
        outer.addWidget(room_box, stretch=2)

        # 公告廣播列
        notice_box = QGroupBox("公告廣播（發送給所有已連線玩家）")
        notice_layout = QHBoxLayout(notice_box)
        self._notice_input = QLineEdit()
        self._notice_input.setPlaceholderText("輸入公告內容，例如：伺服器將在 5 分鐘後更新，請妥善保存進度")
        self._notice_input.returnPressed.connect(self._send_notice)
        notice_layout.addWidget(self._notice_input)
        notice_btn = QPushButton("📢  發送公告")
        notice_btn.setFixedWidth(120)
        notice_btn.clicked.connect(self._send_notice)
        notice_layout.addWidget(notice_btn)
        outer.addWidget(notice_box, stretch=0)

        # 狀態列
        self.statusBar().showMessage("正在啟動…")

    def _build_menu(self):
        menubar = self.menuBar()

        server_menu = menubar.addMenu("伺服器")

        self._act_start = QAction("開啟伺服器", self)
        self._act_start.setEnabled(False)  # 初始禁用，因為伺服器自動啟動
        self._act_start.triggered.connect(self._start_server)
        server_menu.addAction(self._act_start)

        self._act_stop = QAction("關閉伺服器", self)
        self._act_stop.setEnabled(True)
        self._act_stop.triggered.connect(self._stop_server)
        server_menu.addAction(self._act_stop)

        server_menu.addSeparator()

        act_kick = QAction("強制斷線選中玩家", self)
        act_kick.triggered.connect(self._force_disconnect_selected)
        server_menu.addAction(act_kick)

        act_dissolve = QAction("解散選中房間", self)
        act_dissolve.triggered.connect(self._dissolve_selected_room)
        server_menu.addAction(act_dissolve)

        server_menu.addSeparator()

        act_settings = QAction("設定…", self)
        act_settings.triggered.connect(self._open_settings)
        server_menu.addAction(act_settings)

    # ---- 清單刷新 -------------------------------------------------------
    def _refresh_lists(self):
        # 玩家清單
        prev_cid = self._selected_cid
        self.player_list.clear()
        for cid, info in clients.items():
            if info.name:
                room_txt = info.room if info.room else "無房間"
                text = "{} ({})".format(info.name, room_txt)
            else:
                text = "未命名 ({})".format(info.ip)
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, cid)
            self.player_list.addItem(item)
            if cid == prev_cid:
                item.setSelected(True)
                self.player_list.setCurrentItem(item)

        # 房間清單
        self.room_list.clear()
        for room, members in rooms.items():
            host_cid = room_hosts.get(room)
            host_info = clients.get(host_cid) if host_cid else None
            host_name = host_info.name if host_info and host_info.name else "（無）"
            text = "房間 {} · {}/{} 人 · 房主：{}".format(
                room, len(members), MAX_ROOM_SIZE, host_name
            )
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, room)
            self.room_list.addItem(item)

        self._refresh_status()

    def _refresh_status(self):
        n = len(clients)
        self.statusBar().showMessage(
            "正在監聽 port {} · {} 位玩家已連線".format(self._port, n)
        )

    def _refresh_detail(self):
        cid = self._selected_cid
        info = clients.get(cid) if cid else None
        d = self._detail_labels
        if not info:
            for v in d.values():
                v.setText("—")
            return
        d["name"].setText(info.name if info.name else "未命名")
        d["ip"].setText(info.ip)
        elapsed = int(time.time() - info.connected_since)
        h, rem = divmod(elapsed, 3600)
        m, s   = divmod(rem, 60)
        d["time"].setText("{:02d}:{:02d}:{:02d}".format(h, m, s))
        d["room"].setText(info.room if info.room else "無")
        # 遊玩時長（來自 frame，反映整場遊戲累積）
        pt = int(info.play_time)
        ph, pr = divmod(pt, 3600)
        pm, ps = divmod(pr, 60)
        if ph > 0:
            d["playtime"].setText("{}時{:02d}分{:02d}秒".format(ph, pm, ps))
        elif pm > 0:
            d["playtime"].setText("{:02d}分{:02d}秒".format(pm, ps))
        else:
            d["playtime"].setText("{:02d}秒".format(ps))
        d["hit"].setText(str(info.hit_count))
        d["force"].setText(str(info.force_count))
        d["click"].setText(str(info.click_count))
        for i in range(5):
            key = "forge_{}".format(i)
            if key in d:
                v = info.forge_counts[i] if i < len(info.forge_counts) else 0
                d[key].setText(str(v))

    # ---- 互動 -----------------------------------------------------------
    def _on_player_clicked(self, item):
        self._selected_cid = item.data(Qt.UserRole)
        self._refresh_detail()

    def _selected_room(self):
        item = self.room_list.currentItem()
        if item:
            return item.data(Qt.UserRole)
        return None

    def _force_disconnect_selected(self):
        cid = self._selected_cid
        info = clients.get(cid) if cid else None
        if not info:
            QMessageBox.information(self, "提示", "請先在左側選擇一位玩家")
            return
        ws = info.ws

        async def _close():
            try:
                await ws.close()
            except Exception:
                pass
        self._server.schedule(_close())

    def _dissolve_selected_room(self):
        room = self._selected_room()
        if not room or room not in rooms:
            QMessageBox.information(self, "提示", "請先在下方選擇一個房間")
            return

        async def _dissolve():
            members = list(rooms.get(room, []))
            for cid in members:
                m = clients.get(cid)
                if m:
                    m.room = None
                    await _send(m.ws, {"type": "room_dissolved"})
            rooms.pop(room, None)
            room_hosts.pop(room, None)
            _notify_state()
        self._server.schedule(_dissolve())

    def _on_room_double_clicked(self, item):
        """雙擊房間列表項目，顯示房間內玩家清單。"""
        room = item.data(Qt.UserRole)
        if not room or room not in rooms:
            return
        host_cid = room_hosts.get(room)
        lines = []
        for cid in rooms.get(room, []):
            info = clients.get(cid)
            if not info:
                continue
            name = info.name or "未命名"
            suffix = "（房主）" if cid == host_cid else ""
            lines.append(f"  {name}{suffix}")
        msg = "\n".join(lines) if lines else "（空房間）"
        QMessageBox.information(self, f"房間 {room} 玩家列表", msg)

    def _send_notice(self):
        """廣播公告給所有已連線玩家。"""
        text = self._notice_input.text().strip()
        if not text:
            return
        self._notice_input.clear()

        async def _broadcast():
            payload = {"type": "server_notice", "text": text}
            for info in list(clients.values()):
                await _send(info.ws, payload)
        self._server.schedule(_broadcast())

    def _open_settings(self):
        """開啟設定對話框。"""
        dlg = QDialog(self)
        dlg.setWindowTitle("設定")
        dlg.setMinimumWidth(320)

        layout = QVBoxLayout(dlg)

        # Port 設定
        port_row = QHBoxLayout()
        port_row.addWidget(QLabel("Port："))
        port_input = QLineEdit(str(self._port))
        port_input.setValidator(QIntValidator(1, 65535, dlg))
        port_row.addWidget(port_input)
        layout.addLayout(port_row)

        hint_label = QLabel("（更改 Port 需重啟伺服器才生效）")
        hint_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(hint_label)

        # 開機自啟核取方塊
        startup_chk = QCheckBox("開機自啟")
        startup_chk.setChecked(startup_enabled())
        layout.addWidget(startup_chk)

        # 確定 / 取消 按鈕
        btn_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            parent=dlg,
        )
        btn_box.button(QDialogButtonBox.Ok).setText("確定")
        btn_box.button(QDialogButtonBox.Cancel).setText("取消")
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        layout.addWidget(btn_box)

        if dlg.exec_() != QDialog.Accepted:
            return

        # 套用設定
        new_port_text = port_input.text().strip()
        new_port = int(new_port_text) if new_port_text.isdigit() else self._port
        port_changed = new_port != self._port

        # 套用開機自啟
        try:
            if startup_chk.isChecked():
                enable_startup()
            else:
                disable_startup()
        except Exception as e:
            QMessageBox.warning(self, "開機自啟設定失敗", str(e))

        # 套用 Port
        self._port = new_port

        if port_changed and self._server is not None:
            reply = QMessageBox.question(
                self,
                "重啟伺服器",
                f"Port 已更改為 {new_port}，是否立即重啟伺服器以套用？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                self._stop_server()
                # 等待伺服器停止後再啟動（透過 server_stopped 訊號處理）
                self._pending_restart = True

    # ---- 伺服器事件 -----------------------------------------------------
    def _on_server_started(self):
        self._act_start.setEnabled(False)
        self._act_stop.setEnabled(True)
        self._refresh_status()

    def _on_server_stopped(self):
        self.statusBar().showMessage("伺服器已停止")
        self._act_start.setEnabled(True)
        self._act_stop.setEnabled(False)
        self._server = None
        # 若有待重啟旗標，自動重新啟動
        if getattr(self, '_pending_restart', False):
            self._pending_restart = False
            self._start_server()

    def _on_server_error(self, msg):
        self.statusBar().showMessage("伺服器錯誤：{}".format(msg))
        QMessageBox.critical(self, "伺服器錯誤", msg)


# ---------------------------------------------------------------------------
# 進入點
# ---------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)

    win = MainWindow()
    win.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
