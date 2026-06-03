"""
NetworkClient — WebSocket 客戶端。
- 在 daemon 執行緒中運行 asyncio event loop
- 從背景執行緒透過 Qt signals 通知主執行緒
- 從 Qt 執行緒用 asyncio.run_coroutine_threadsafe() 傳送訊息
"""
import asyncio
import json
import threading

from PyQt5.QtCore import QObject, pyqtSignal

# ── websockets 可選依賴 ────────────────────────────────────────────────────────
try:
    import websockets
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False

# ── 錯誤碼 → 中文訊息對應表 ──────────────────────────────────────────────────
_ERROR_MESSAGES = {
    "INVALID_NAME":    "名稱無效（1-12字元，中文/英文/數字）",
    "ROOM_EXISTS":     "此房間號碼已存在，請換一個",
    "ALREADY_IN_ROOM": "你已在房間中",
    "NO_NAME":         "請先輸入玩家名稱",
    "ROOM_NOT_FOUND":  "找不到此房間號碼",
    "NAME_TAKEN":      "此房間已有同名玩家，請換個名稱",
    "ROOM_FULL":       "此房間已滿（最多8人）",
    "NOT_HOST":        "只有房主才能踢除玩家",
    "PLAYER_NOT_FOUND": "找不到此玩家",
}


class NetworkClient(QObject):
    """WebSocket 客戶端，線程安全。"""

    # ── Signals ───────────────────────────────────────────────────────────────
    connected      = pyqtSignal()
    disconnected   = pyqtSignal(str)          # 斷線原因字串
    conn_error     = pyqtSignal(str)          # 連線失敗（給 UI 顯示）

    room_joined    = pyqtSignal(str, list, str)  # (room_id, [player_names], host_name)
    player_joined  = pyqtSignal(str)             # player_name
    player_left    = pyqtSignal(str)             # player_name
    kicked         = pyqtSignal()
    room_dissolved = pyqtSignal()
    server_error   = pyqtSignal(str, str)        # (error_code, human_msg)

    frame_received = pyqtSignal(str, dict)       # (from_name, frame_data)
    chat_received  = pyqtSignal(str, str)        # (from_name, text)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

        self._player_name: str | None = None
        self._current_room: str | None = None
        self._room_players: list[str] = []
        self._room_host: str | None = None
        self._server_host: str = ""    # 最後一次連線的 IP，供存檔使用

    # ── 屬性 ──────────────────────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and not getattr(self._ws, "closed", True)

    @property
    def current_room(self) -> str | None:
        return self._current_room

    @property
    def room_players(self) -> list[str]:
        return list(self._room_players)

    @property
    def room_host(self) -> str | None:
        return self._room_host

    @property
    def player_name(self) -> str | None:
        return self._player_name

    @property
    def server_host(self) -> str:
        """最後一次成功連線的伺服器 IP，供存檔用。"""
        return self._server_host

    # ── 連線 ──────────────────────────────────────────────────────────────────

    def connect_to_server(self, host: str, port: int = 9527):
        """從 Qt 主執行緒呼叫；在背景執行緒嘗試連線。"""
        if not _WS_AVAILABLE:
            self.conn_error.emit("請先安裝 websockets 套件：pip install websockets")
            return

        if self._thread and self._thread.is_alive():
            # 已有連線或正在連線中，忽略
            return

        self._thread = threading.Thread(
            target=self._run_event_loop,
            args=(host, port),
            daemon=True,
            name="NetworkClient-loop",
        )
        self._thread.start()

    def _run_event_loop(self, host: str, port: int):
        """在 daemon 執行緒中建立並運行 asyncio event loop。"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with self._lock:
            self._loop = loop
        try:
            loop.run_until_complete(self._connect_and_listen(host, port))
        finally:
            with self._lock:
                self._loop = None

    async def _connect_and_listen(self, host: str, port: int):
        uri = f"ws://{host}:{port}"
        retry_count = 0

        while True:
            try:
                async with asyncio.timeout(8):
                    ws = await websockets.connect(uri)
            except asyncio.TimeoutError:
                self.conn_error.emit("無法連線，請確認伺服器 IP 正確且伺服器已開啟")
                return
            except ConnectionRefusedError:
                self.conn_error.emit("無法連線，請確認伺服器 IP 正確且伺服器已開啟")
                return
            except OSError:
                self.conn_error.emit("無法連線，請確認伺服器 IP 正確且伺服器已開啟")
                return
            except Exception as e:
                self.conn_error.emit(f"連線失敗：{e}")
                return

            # 連線成功
            with self._lock:
                self._ws = ws
                self._server_host = host
            self.connected.emit()
            retry_count = 0

            try:
                await self._listen(ws)
            except Exception:
                pass
            finally:
                with self._lock:
                    self._ws = None

            # 非預期斷線，嘗試重連
            retry_count += 1
            if retry_count > 3:
                self.disconnected.emit("已斷線，重試 3 次仍失敗")
                return
            await asyncio.sleep(2)

    async def _listen(self, ws):
        """接收並分派伺服器訊息。"""
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            await self._dispatch(msg)

    async def _dispatch(self, msg: dict):
        t = msg.get("type", "")

        if t == "ping":
            await self._send_raw({"type": "pong"})

        elif t == "room_state":
            room_id = str(msg.get("room", ""))
            players = msg.get("players", [])
            host    = msg.get("host", "")
            self._current_room = room_id
            self._room_players = list(players)
            self._room_host    = host
            self.room_joined.emit(room_id, players, host)

        elif t == "player_joined":
            name = msg.get("name", "")
            if name not in self._room_players:
                self._room_players.append(name)
            self.player_joined.emit(name)

        elif t == "player_left":
            name = msg.get("name", "")
            if name in self._room_players:
                self._room_players.remove(name)
            self.player_left.emit(name)

        elif t == "kicked":
            self._current_room = None
            self._room_players = []
            self._room_host    = None
            self.kicked.emit()

        elif t == "room_dissolved":
            self._current_room = None
            self._room_players = []
            self._room_host    = None
            self.room_dissolved.emit()

        elif t == "error":                        # 伺服器實際送出的型別
            code = msg.get("code", "UNKNOWN")
            human = _ERROR_MESSAGES.get(code, f"伺服器錯誤：{code}")
            self.server_error.emit(code, human)

        elif t == "frame":
            from_name  = msg.get("from", "")
            frame_data = msg.get("data", {})
            self.frame_received.emit(from_name, frame_data)

        elif t == "chat":
            from_name = msg.get("from", "")
            text      = msg.get("text", "")
            self.chat_received.emit(from_name, text)

    # ── 內部傳送工具 ──────────────────────────────────────────────────────────

    async def _send_raw(self, payload: dict):
        with self._lock:
            ws = self._ws
        if ws is None:
            return
        try:
            await ws.send(json.dumps(payload, ensure_ascii=False))
        except Exception:
            pass   # 連線已關閉，靜默忽略

    def _schedule(self, coro):
        """從 Qt 主執行緒安全地排程一個協程到背景 loop。"""
        with self._lock:
            loop = self._loop
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, loop)

    # ── 傳送方法（從 Qt 主執行緒呼叫） ───────────────────────────────────────

    def disconnect(self):
        """主動斷線。"""
        async def _close():
            with self._lock:
                ws = self._ws
            if ws:
                await ws.close()
        self._schedule(_close())

    def set_name(self, name: str):
        self._player_name = name
        self._schedule(self._send_raw({"type": "set_name", "name": name}))

    def create_room(self, room: str):
        self._schedule(self._send_raw({"type": "create_room", "room": room}))

    def join_room(self, room: str):
        self._schedule(self._send_raw({"type": "join_room", "room": room}))

    def leave_room(self):
        self._schedule(self._send_raw({"type": "leave_room"}))
        self._current_room = None
        self._room_players = []
        self._room_host    = None

    def kick(self, target: str):
        self._schedule(self._send_raw({"type": "kick", "target": target}))

    def send_frame(self, data: dict):
        """由 widget.py 每 50ms 呼叫。"""
        self._schedule(self._send_raw({"type": "frame", "data": data}))

    def send_chat(self, text: str):
        self._schedule(self._send_raw({"type": "chat", "text": text}))
