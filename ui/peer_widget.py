"""
PeerWidget — 在玩家螢幕上顯示其他玩家（peer）的遊戲狀態。

由 WebSocket frame 驅動：widget.py 收到網路 frame 時呼叫 update_from_frame()，
收到聊天訊息時呼叫 show_bubble()。

- 透明、無邊框、永遠置頂的 Qt.Tool 視窗。
- 重用 ui/renderer.py 的 draw_frame() 繪製鐵鎚 + 鐵砧場景。
- 火花特效在本地端生成（不依賴網路傳輸火花資料），以節省頻寬。
- 左鍵拖曳移動；右鍵選單可靜音、隱藏鐵砧、隱藏名稱、縮放、置中。
- 頂部顯示聊天氣泡，底部顯示玩家名稱。
"""
import math
import random
import time

from PyQt5.QtWidgets import (QWidget, QMenu, QAction, QDialog,
                             QVBoxLayout, QHBoxLayout, QPushButton, QSlider, QLabel)
from PyQt5.QtCore    import Qt, QTimer, QPoint, QRect, QRectF
from PyQt5.QtGui     import (QPainter, QColor, QPen, QBrush, QFont,
                             QPainterPath, QPixmap, QTransform)

from config import (
    AX, AY_BASE, FACE_TOP, FACE_L, FACE_R,
    KB_X, KB_Y, MAX_VCY, APPROACH_DIST,
    IDLE_ANGLE, SWING_ANGLE,
    HEAD_OFFSET, HEAD_PERP,
    HL, HR, HP,
)
from game.state  import Spark
from ui.renderer import draw_frame


# ── 遊戲空間尺寸（與 renderer 相同的 800×600 座標系）─────────────────────────
_GAME_W = 800
_GAME_H = 600
_BASE_SCALE = 0.6           # peer 預設縮小到 60%

_LOOP_MS = 16              # ~60 fps — 讓 lerp 有真正的子步驟可補幀
_LOOP_S  = _LOOP_MS / 1000.0

_BUBBLE_HOLD_MS  = 5000    # 氣泡完整顯示時間
_BUBBLE_FADE_MS  = 100     # 淡出計時器間隔
_BUBBLE_FADE_STEP = 0.08   # 每次淡出降低的 alpha

_DRAG_THRESH2 = 25

# 鐵砧底座視覺下緣（game 座標）= renderer._V2_BASE_BOT_Y
# FACE_TOP(330) + face_body(57) + waist(32) + base(38) = 457
_ANVIL_BOT_Y = FACE_TOP + 127


# ── Peer 顯示狀態（模仿 GameState，供 renderer 使用）────────────────────────

class PeerDisplayState:
    """模仿 GameState 的介面，供 ui.renderer.draw_frame 使用。

    renderer 大量以 getattr(state, attr, default) 讀取屬性，因此這裡只需提供
    繪製所需的欄位即可。"""

    def __init__(self, ui_scale: float = _BASE_SCALE):
        # lerp 補幀 ──────────────────────────────────────────────────────────
        self.lerp_enabled = False       # 由外部 set_lerp() 控制
        self._tgt_vcx  = float(KB_X)   # lerp 目標（由每幀 frame 更新）
        self._tgt_vcy  = float(KB_Y)
        self._tgt_vcvx = 0.0
        self._tgt_vcvy = 0.0

        # 動態（由 frame 更新）──────────────────────────────────────────────
        self.vcx = float(KB_X)
        self.vcy = float(KB_Y)
        self.vcvx = 0.0
        self.vcvy = 0.0
        self.has_hit = False
        self.anvil_glow = 0.0
        self.heat_level = 0.0
        self.strike_color = (210, 120, 70)
        self.hide_anvil = False
        self.kb_active = False
        self.kb_state = "idle"
        self.kb_mode = "charge"
        self.typing_charge = 0
        self.typing_max_charge = 5
        self.charge_pulses = []
        self.turbo_mode = False
        self.fever_active = False
        self.fever_timer = 0.0
        self.fever_cooldown_timer = 0.0
        self.fever_cooldown_duration = 75.0
        self.consecutive_full_charge = 0
        self.fever_threshold = 2
        self.play_time = 0.0
        self.turbo_line_idx = -1
        self.combo_dot_idx = -1
        self.hit_count = 0
        self.click_count = 0
        self.force_count = 0

        # 靜態（對 peer 不顯示複雜特效）────────────────────────────────────
        self.sparks: list = []
        self.embers: list = []
        self.hit_numbers: list = []
        self.mouse_on_widget = False
        self.lock_position = True       # 避免顯示 ghost guide
        self.show_hit = False
        self.show_force = False
        self.show_click = False
        self.show_charge_bar = False
        self.show_hit_numbers = False
        self.show_metal_forge = True    # 顯示金屬塊（與 renderer 預設一致）
        self.current_metal = None
        self.metal_spawned = False
        self.ui_scale = ui_scale

    # ── 與 GameState 相同的幾何公式 ───────────────────────────────────────

    def hammer_angle(self) -> float:
        dist = FACE_TOP - self.vcy
        t = 1.0 - max(0.0, min(1.0, dist / APPROACH_DIST))
        return IDLE_ANGLE + t * (SWING_ANGLE - IDLE_ANGLE)

    def head_face_pos(self):
        a = self.hammer_angle()
        hx = self.vcx + HEAD_OFFSET * math.cos(a) + HEAD_PERP * math.sin(a)
        hy = self.vcy + HEAD_OFFSET * math.sin(a) - HEAD_PERP * math.cos(a)
        return hx, hy

    # ── 網路 frame → 狀態 ─────────────────────────────────────────────────

    def update_from_frame(self, data: dict):
        """接收來自網路的 frame data，更新狀態。"""
        prev_hit = self.has_hit
        new_vcx  = float(data.get("vcx",  self.vcx))
        new_vcy  = float(data.get("vcy",  self.vcy))
        new_vcvx = float(data.get("vcvx", 0.0))
        new_vcvy = float(data.get("vcvy", 0.0))
        new_has_hit = bool(data.get("has_hit", self.has_hit))
        if self.lerp_enabled:
            # 儲存目標值；tick() 每幀用指數 lerp 趨近
            self._tgt_vcx  = new_vcx
            self._tgt_vcy  = new_vcy
            self._tgt_vcvx = new_vcvx
            self._tgt_vcvy = new_vcvy
            # 打擊確認：連打時網路幀的 vcy 可能已是彈起位置
            # 強制顯示觸砧瞬間（MAX_VCY = FACE_TOP - HEAD_PERP）
            # 下一幀 lerp 自動追回網路位置，呈現自然彈起
            if new_has_hit and not self.has_hit:
                self.vcy  = float(MAX_VCY)
                self.vcvy = new_vcvy   # 彈起速度讓後續動畫自然
        else:
            self.vcx  = new_vcx
            self.vcy  = new_vcy
            self.vcvx = new_vcvx
            self.vcvy = new_vcvy
        self.has_hit = new_has_hit
        self.anvil_glow = float(data.get("anvil_glow", self.anvil_glow))
        sc = data.get("strike_color")
        if isinstance(sc, list) and len(sc) == 3:
            self.strike_color = tuple(sc)
        # hide_anvil 不從網路讀取——每位玩家的螢幕由自己決定是否隱藏對方鐵砧
        self.kb_active = bool(data.get("kb_active", self.kb_active))
        self.kb_state = str(data.get("kb_state", self.kb_state))
        self.kb_mode = str(data.get("kb_mode", self.kb_mode))
        self.turbo_mode = bool(data.get("turbo_mode", self.turbo_mode))
        self.fever_active = bool(data.get("fever_active", self.fever_active))
        self.hit_count = int(data.get("hit_count", self.hit_count))
        self.click_count = int(data.get("click_count", self.click_count))
        self.force_count = int(data.get("force_count", self.force_count))
        charge_frac = float(data.get("charge", 0.0))
        self.typing_charge = int(charge_frac * self.typing_max_charge)

        # 金屬塊同步
        metal_type = int(data.get("metal_type", -1))
        if metal_type >= 0:
            from game.metal import MetalPiece
            if (self.current_metal is None
                    or self.current_metal.dead
                    or self.current_metal.type_idx != metal_type):
                self.current_metal = MetalPiece(metal_type)
            m = self.current_metal
            m.quality     = float(data.get("metal_ratio", 0.0)) * m.quality_max
            m.spawn_t     = float(data.get("metal_spawn_t", 1.0))
            m.flash_t     = float(data.get("metal_flash_t", 0.0))
            m.complete    = bool(data.get("metal_complete", False))
            m.dead        = False
            self.metal_spawned = True
        else:
            if self.current_metal is not None:
                self.current_metal.dead = True
            self.metal_spawned = False

        # 本地計算：打擊時生成火花
        if self.has_hit and not prev_hit:
            self.heat_level = min(1.0, self.heat_level + 0.20)
            self._emit_local_sparks()
        # 衰減 heat_level（模擬每 frame ~16ms 的衰減）
        self.heat_level = max(0.0, self.heat_level - 0.016 * 0.20)

    def _emit_local_sparks(self):
        """打擊時在本地生成火花（不依賴網路傳輸的火花資料）。"""
        intensity = self.anvil_glow
        count = int((10 + intensity * 40))
        sx = float(AX)
        # 有金屬塊時，接觸面在金屬塊頂面（與 renderer._render_vcy_fast 邏輯一致）
        m = self.current_metal
        if (m is not None and not m.dead
                and m.spawn_t >= 1.0 and m.flash_t <= 0.0):
            sy = float(FACE_TOP - m.thickness)
        else:
            sy = float(FACE_TOP)
        for _ in range(count):
            a = -math.pi + random.random() * math.pi
            spd = 60 + random.random() * 400 * (0.2 + intensity * 0.8)
            life = 0.22 + random.random() * 0.50
            r = random.random()
            if r < 0.25:
                color = (255, 255, 255)
            elif r < 0.60:
                color = (255, 221, 34)
            else:
                color = (255, 136, 0)
            size = 1.3 + random.random() * 2.8 * max(0.1, intensity)
            self.sparks.append(
                Spark(sx, sy, math.cos(a) * spd, math.sin(a) * spd - 70,
                      life, size, color)
            )

    def tick(self, delta_s: float):
        """每幀更新（sparks 衰減 + lerp）。"""
        # 速度 + 位置誤差雙重自適應 lerp：
        #   k_min = 15  （靜止/幾乎到位：超平滑）
        #   k_max = 185 （高速 or 位置偏差大：接近 1 幀收斂 ~95%）
        #
        # 改進重點：
        #   1. 加入位置誤差貢獻（pos_err_y）：位置跑太遠時自動加速追回
        #   2. vcvx/vcvy 直接 snap 目標值（速度不影響渲染，lerp 只會讓 k 滯後）
        #   3. 微小殘差直接 snap（消除 lerp 的永遠收斂微抖動）
        if self.lerp_enabled:
            pos_err_y = abs(self._tgt_vcy - self.vcy)
            v_abs = max(abs(self.vcvy), abs(self._tgt_vcvy))
            k = 15.0 + min(v_abs / 3.5 + pos_err_y * 0.4, 170.0)
            alpha = 1.0 - math.exp(-k * delta_s)
            self.vcx += (self._tgt_vcx - self.vcx) * alpha
            self.vcy += (self._tgt_vcy - self.vcy) * alpha
            # 速度直接對齊目標：vcvx/vcvy 不影響渲染，直接 snap
            # 讓下一幀的 k 正確反映網路速度，避免速度 lerp 滯後
            self.vcvx = self._tgt_vcvx
            self.vcvy = self._tgt_vcvy
            # 極小殘差直接 snap，消除永遠收斂的微抖動
            if abs(self._tgt_vcx - self.vcx) < 0.4:
                self.vcx = self._tgt_vcx
            if abs(self._tgt_vcy - self.vcy) < 0.4:
                self.vcy = self._tgt_vcy
        alive = []
        for s in self.sparks:
            s.vy += 870 * delta_s
            s.x += s.vx * delta_s
            s.y += s.vy * delta_s
            s.life -= delta_s
            if s.life > 0 and s.y < 630:
                alive.append(s)
        self.sparks = alive
        self.play_time += delta_s


# ── PeerWidget ────────────────────────────────────────────────────────────────

class PeerWidget(QWidget):
    """在玩家螢幕上顯示某一位 peer 的鐵砧場景。"""

    _SCALE_OPTIONS = [
        ("50%", 0.5),
        ("75%", 0.75),
        ("100%", 1.0),
        ("125%", 1.25),
        ("150%", 1.5),
    ]

    def __init__(self, player_name: str = "", parent=None):
        super().__init__(parent)
        self._player_name = player_name

        self._always_on_top = True
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)

        # ── 縮放 ──────────────────────────────────────────────────────────
        self._viewer_scale = 0.75   # 預設 75%
        self._peer_state = PeerDisplayState(ui_scale=_BASE_SCALE * self._viewer_scale)
        self._apply_scale(self._viewer_scale)

        # ── 設定 ──────────────────────────────────────────────────────────
        self._muted = False
        self._name_visible = False   # 預設不固定顯示（hover 才出現）
        self._hovered = False
        # 本地端隱藏覆蓋：None = 顯示（預設）；True = 本地強制隱藏；False = 本地強制顯示
        # 完全由本地玩家決定，不受對方廣播的 hide_anvil 值影響
        self._viewer_hide_anvil: "bool | None" = None

        # ── 視覺調整 ──────────────────────────────────────────────────────
        self._flip_h:    bool  = False
        self._flip_v:    bool  = False
        self._rotation:  float = 0.0

        # ── 拖曳狀態 ──────────────────────────────────────────────────────
        self._press_global: QPoint | None = None
        self._drag_offset:  QPoint | None = None
        self._is_dragging:  bool          = False

        # ── 合作打鐵 ──────────────────────────────────────────────────────
        # 模式："none" | "pending"（中心進入主砧，等待鬆開）| "active"（已合作）
        self._coop_mode:          str        = "none"
        self._coop_flip_h_active: bool       = False   # 合作時強制水平反轉
        self._coop_overlap:       bool       = False   # 中心是否在主砧範圍內
        self._coop_snap_y:        int | None = None    # 拖曳時鎖定的 Y 座標
        # 由 BlacksmithWidget 在建立時設定
        self._host_widget_ref  = None   # BlacksmithWidget 參照（偵測重疊用）
        self._coop_release_cb  = None   # fn(peer_name) — 鬆開時呼叫
        self._coop_pref_getter = None   # fn() -> bool|None
        self._coop_pref_setter = None   # fn(bool|None) -> None
        self._coop_end_cb      = None   # fn(peer_name) — 要求結束合作

        # ── 聊天氣泡 ──────────────────────────────────────────────────────
        self._bubble_text  = ""
        self._bubble_alpha = 0.0
        self._bubble_close_rect = QRect()   # 「×」按鈕矩形（widget 座標）
        self._bubble_timer = QTimer(self)   # 5 秒後開始淡出
        self._bubble_timer.setSingleShot(True)
        self._bubble_timer.timeout.connect(self._start_bubble_fade)
        self._bubble_fade_timer = QTimer(self)   # 每 100ms 降低 alpha
        self._bubble_fade_timer.setInterval(_BUBBLE_FADE_MS)
        self._bubble_fade_timer.timeout.connect(self._fade_bubble_step)

        # ── Hover 延遲消失（與自己 widget ghost guide 相同的 400ms）────────
        # 控制名稱（未固定時）與 ghost guide 在滑鼠離開後延遲消失
        self._hover_hide_timer = QTimer(self)
        self._hover_hide_timer.setSingleShot(True)
        self._hover_hide_timer.setInterval(400)
        self._hover_hide_timer.timeout.connect(self._on_hover_hide)

        # ── 遊戲迴圈 ──────────────────────────────────────────────────────
        self._last_tick_ns: int = time.monotonic_ns()   # 真實 delta time 計時
        self._loop = QTimer(self)
        self._loop.setInterval(_LOOP_MS)
        self._loop.timeout.connect(self._tick)
        self._loop.start()

    # ── 縮放 ────────────────────────────────────────────────────────────────

    def _apply_scale(self, viewer_scale: float):
        """套用新的觀看縮放：更新 ui_scale 與 widget 尺寸。"""
        self._viewer_scale = max(0.5, min(1.5, viewer_scale))
        scale = _BASE_SCALE * self._viewer_scale
        self._peer_state.ui_scale = scale
        w = int(_GAME_W * scale)
        h = int(_GAME_H * scale)
        self.setFixedSize(w, h)
        self.update()

    # ── 遊戲迴圈 ──────────────────────────────────────────────────────────────

    def _tick(self):
        # 使用真實 elapsed time 讓 lerp 與螢幕刷新率無關
        now = time.monotonic_ns()
        dt = (now - self._last_tick_ns) * 1e-9   # 秒
        self._last_tick_ns = now
        dt = min(dt, 0.1)   # 最多 100ms，避免視窗最小化後暴衝
        self._peer_state.tick(dt)
        # ghost guide：僅在本地隱藏鐵砧時才顯示虛線指示器
        if self._peer_state.hide_anvil:
            self._peer_state.mouse_on_widget = self._hovered
            self._peer_state.lock_position   = False
        else:
            self._peer_state.mouse_on_widget = False
            self._peer_state.lock_position   = True
        self.update()

    # ── 公開 API ──────────────────────────────────────────────────────────────

    def update_from_frame(self, data: dict):
        """由 widget.py 在收到 frame 時呼叫。"""
        self._peer_state.update_from_frame(data)
        # 套用本地端隱藏覆蓋（避免被 peer 的 frame 覆蓋重置）
        if self._viewer_hide_anvil is not None:
            self._peer_state.hide_anvil = self._viewer_hide_anvil
        self.update()

    def get_prefs(self) -> dict:
        """回傳目前的外觀/位置偏好，供 widget.py 持久化到存檔。"""
        return {
            "x":             self.x(),
            "y":             self.y(),
            "scale":         self._viewer_scale,
            "hide_anvil":    self._viewer_hide_anvil,
            "name_visible":  self._name_visible,
            "always_on_top": self._always_on_top,
            "muted":         self._muted,
            "flip_h":        self._flip_h,
            "flip_v":        self._flip_v,
            "rotation":      self._rotation,
        }

    def apply_prefs(self, prefs: dict):
        """套用存檔中的偏好；缺少的鍵保持目前預設值。"""
        if not prefs:
            return
        # 位置
        x, y = prefs.get("x"), prefs.get("y")
        if x is not None and y is not None:
            self.move(int(x), int(y))
        # 縮放
        scale = prefs.get("scale")
        if scale is not None:
            self._apply_scale(float(scale))
        # 本地隱藏砧
        hide_anvil = prefs.get("hide_anvil")
        if hide_anvil is not None:
            self._viewer_hide_anvil = bool(hide_anvil)
            self._peer_state.hide_anvil = bool(hide_anvil)
        # 固定顯示名稱
        if "name_visible" in prefs:
            self._name_visible = bool(prefs["name_visible"])
        # 靜音
        if "muted" in prefs:
            self._muted = bool(prefs["muted"])
        # 置頂（flags 改變需要重新 show）
        always_on_top = prefs.get("always_on_top")
        if always_on_top is not None:
            target = bool(always_on_top)
            if self._always_on_top != target:
                self._always_on_top = target
                flags = Qt.FramelessWindowHint | Qt.Tool
                if target:
                    flags |= Qt.WindowStaysOnTopHint
                self.setWindowFlags(flags)
                self.show()   # flags 改變後需重新 show
        # 視覺調整
        if "flip_h" in prefs:
            self._flip_h = bool(prefs["flip_h"])
        if "flip_v" in prefs:
            self._flip_v = bool(prefs["flip_v"])
        if "rotation" in prefs:
            self._rotation = float(prefs["rotation"])

    def set_lerp(self, enabled: bool):
        """切換 lerp 補幀；關閉時立即對齊目標值，避免殘留偏移。"""
        self._peer_state.lerp_enabled = enabled
        if not enabled:
            self._peer_state.vcx  = self._peer_state._tgt_vcx
            self._peer_state.vcy  = self._peer_state._tgt_vcy
            self._peer_state.vcvx = self._peer_state._tgt_vcvx
            self._peer_state.vcvy = self._peer_state._tgt_vcvy

    def show_bubble(self, text: str):
        """由 widget.py 在收到 chat 時呼叫。靜音時直接忽略。"""
        if self._muted:
            return
        self._bubble_text  = str(text)
        self._bubble_alpha = 1.0
        self._bubble_fade_timer.stop()
        self._bubble_timer.start(_BUBBLE_HOLD_MS)
        self.update()

    @property
    def player_name(self) -> str:
        return self._player_name

    @property
    def is_muted(self) -> bool:
        return self._muted

    # ── 氣泡淡出 ──────────────────────────────────────────────────────────────

    def _start_bubble_fade(self):
        self._bubble_fade_timer.start()

    def _fade_bubble_step(self):
        self._bubble_alpha -= _BUBBLE_FADE_STEP
        if self._bubble_alpha <= 0.0:
            self._bubble_alpha = 0.0
            self._bubble_text = ""
            self._bubble_close_rect = QRect()
            self._bubble_fade_timer.stop()
        self.update()

    def _clear_bubble(self):
        self._bubble_timer.stop()
        self._bubble_fade_timer.stop()
        self._bubble_alpha = 0.0
        self._bubble_text = ""
        self._bubble_close_rect = QRect()
        self.update()

    # ── 繪製 ──────────────────────────────────────────────────────────────────

    def paintEvent(self, _event):
        if self._has_visual_transform():
            pm = QPixmap(self.size())
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            self._paint_content(p)
            p.end()
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setTransform(self._make_visual_transform())
            painter.drawPixmap(0, 0, pm)
            painter.end()
        else:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            self._paint_content(painter)
            painter.end()

    def _paint_content(self, painter: QPainter):
        """實際繪製內容（場景 + 名稱 + 氣泡），不含視覺 transform。"""
        # ── 合作模式視覺覆蓋 ──────────────────────────────────────────────
        _orig_hide = self._peer_state.hide_anvil
        if self._coop_mode in ("pending", "active"):
            self._peer_state.hide_anvil = True        # 強制隱藏鐵砧
            if self._coop_mode == "pending":
                painter.setOpacity(0.45)              # 半透明

        draw_frame(painter, self._peer_state)

        if self._coop_mode in ("pending", "active"):
            self._peer_state.hide_anvil = _orig_hide  # 還原原始值（不污染狀態）
        painter.setOpacity(1.0)                        # 名稱 / 氣泡全不透明

        painter.save()
        painter.resetTransform()
        self._draw_name(painter)
        painter.restore()

        painter.save()
        painter.resetTransform()
        self._draw_bubble(painter)
        painter.restore()

    def _effective_flip_h(self) -> bool:
        """合作模式強制水平反轉（XOR 使用者設定）。"""
        if self._coop_flip_h_active:
            return not self._flip_h   # 反向於使用者設定，讓鐵錘面向主砧
        return self._flip_h

    def _has_visual_transform(self) -> bool:
        return self._effective_flip_h() or self._flip_v or abs(self._rotation) > 0.01

    def _make_visual_transform(self) -> QTransform:
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        t = QTransform()
        t.translate(cx, cy)
        if abs(self._rotation) > 0.01:
            t.rotate(self._rotation)
        sx = -1.0 if self._effective_flip_h() else 1.0
        sy = -1.0 if self._flip_v else 1.0
        if sx != 1.0 or sy != 1.0:
            t.scale(sx, sy)
        t.translate(-cx, -cy)
        return t

    def _draw_name(self, painter: QPainter):
        if not self._player_name:
            return
        # _name_visible=False 時，只在 hover 顯示
        if not self._name_visible and not self._hovered:
            return

        painter.setFont(QFont("Segoe UI", 11))
        fm = painter.fontMetrics()
        text = self._player_name
        tw = fm.horizontalAdvance(text)
        ui = self._peer_state.ui_scale

        if self._peer_state.hide_anvil:
            # 隱藏鐵砧時：名稱在打擊點（AX, FACE_TOP）正下方 12px
            # 水平對齊打擊點中心（AX），讓名稱貼著虛線指示器
            cx = AX * ui
            ty = int(FACE_TOP * ui) + 12 + fm.ascent()
        else:
            # 鐵砧可見時：緊貼鐵砧底座，只留 3px 間距
            cx = self.width() / 2
            ty = int(_ANVIL_BOT_Y * ui) + 3 + fm.ascent()

        # 陰影提升可讀性
        painter.setPen(QPen(QColor(0, 0, 0, 160)))
        for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            painter.drawText(QPoint(int(cx - tw / 2 + ox), int(ty + oy)), text)
        # 白色 70% opacity
        painter.setPen(QPen(QColor(255, 255, 255, int(0.70 * 255))))
        painter.drawText(QPoint(int(cx - tw / 2), int(ty)), text)

    def _draw_bubble(self, painter: QPainter):
        if not self._bubble_text or self._bubble_alpha <= 0.0:
            self._bubble_close_rect = QRect()
            return

        alpha = max(0.0, min(1.0, self._bubble_alpha))

        margin = 8
        pad_x  = 10
        pad_y  = 6
        close_sz = 14   # 「×」點擊區域邊長

        font = QFont("Segoe UI", 10)
        painter.setFont(font)
        fm = painter.fontMetrics()

        max_text_w = self.width() - margin * 2 - pad_x * 2 - close_sz - 4
        max_text_w = max(40, max_text_w)

        # 自動換行
        lines = self._wrap_text(self._bubble_text, fm, max_text_w)
        line_h = fm.height()
        text_w = max((fm.horizontalAdvance(ln) for ln in lines), default=0)

        box_w = text_w + pad_x * 2 + close_sz + 4
        box_w = min(box_w, self.width() - margin * 2)
        box_h = line_h * len(lines) + pad_y * 2

        box_x = (self.width() - box_w) / 2
        box_y = margin   # widget 頂部

        rect = QRectF(box_x, box_y, box_w, box_h)

        # 半透明深色圓角矩形背景
        path = QPainterPath()
        path.addRoundedRect(rect, 8, 8)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(20, 20, 24, int(210 * alpha))))
        painter.drawPath(path)
        painter.setPen(QPen(QColor(120, 120, 130, int(140 * alpha))))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

        # 白色文字
        painter.setPen(QPen(QColor(255, 255, 255, int(235 * alpha))))
        tx = box_x + pad_x
        ty = box_y + pad_y + fm.ascent()
        for ln in lines:
            painter.drawText(QPoint(int(tx), int(ty)), ln)
            ty += line_h

        # 右上角「×」符號
        cx = box_x + box_w - close_sz - 6
        cy = box_y + 5
        self._bubble_close_rect = QRect(int(cx), int(cy), close_sz, close_sz)
        pen = QPen(QColor(220, 220, 225, int(220 * alpha)))
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        m = 3
        painter.drawLine(int(cx + m), int(cy + m),
                         int(cx + close_sz - m), int(cy + close_sz - m))
        painter.drawLine(int(cx + close_sz - m), int(cy + m),
                         int(cx + m), int(cy + close_sz - m))

    @staticmethod
    def _wrap_text(text: str, fm, max_w: int) -> list:
        """以字元為單位的簡單換行（中文無空白，逐字測量）。"""
        lines = []
        for raw_line in text.split("\n"):
            cur = ""
            for ch in raw_line:
                if fm.horizontalAdvance(cur + ch) > max_w and cur:
                    lines.append(cur)
                    cur = ch
                else:
                    cur += ch
            lines.append(cur)
        return lines or [""]

    # ── 滑鼠：拖曳 + 氣泡關閉 + hover ──────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # 先檢查是否點到氣泡「×」
            if (not self._bubble_close_rect.isNull()
                    and self._bubble_alpha > 0.0
                    and self._bubble_close_rect.contains(event.pos())):
                self._clear_bubble()
                return
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
            new_pos = event.globalPos() - self._drag_offset

            # ── 合作重疊偵測（僅在非 active 狀態時才判斷） ────────────────
            # 判斷依據：A 的「鐵砧中心點」是否進入 B 的「鐵砧範圍」
            if self._host_widget_ref is not None and self._coop_mode != "active":
                anvil_center = self._compute_anvil_center_screen(new_pos)
                host_anvil   = self._host_anvil_bounds()
                new_overlap  = (host_anvil is not None
                                and host_anvil.contains(anvil_center))
                if new_overlap != self._coop_overlap:
                    self._coop_overlap = new_overlap
                    if new_overlap:
                        self._enter_coop_pending(new_pos)
                    else:
                        self._exit_coop_pending()

            # ── 鎖定 Y 軸（pending 模式：只能水平移動） ───────────────────
            if self._coop_mode == "pending" and self._coop_snap_y is not None:
                self.move(new_pos.x(), self._coop_snap_y)
            else:
                self.move(new_pos)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            was_dragging = self._is_dragging
            was_overlap  = self._coop_overlap
            was_pending  = (self._coop_mode == "pending")

            self._is_dragging  = False
            self._press_global = None
            self._drag_offset  = None

            if was_dragging and was_overlap and was_pending:
                # 鬆開時中心在主砧內 → 觸發合作邀請流程
                if self._coop_release_cb:
                    self._coop_release_cb(self._player_name)
                # 保持 pending 狀態，等待對話框結果
            elif was_dragging and not was_overlap and was_pending:
                # 拖出主砧後鬆開 → 取消 pending
                self._exit_coop_pending()

    # ── 合作打鐵輔助 ──────────────────────────────────────────────────────────

    def _compute_anvil_center_screen(self, widget_pos: QPoint) -> QPoint:
        """計算此 peer widget 的鐵砧中心（遊戲座標 AX, (FACE_TOP+_ANVIL_BOT_Y)/2）
        在螢幕上的座標，給定 widget 左上角位置 widget_pos。"""
        scale = self._peer_state.ui_scale
        cx = widget_pos.x() + int(AX * scale)
        cy = widget_pos.y() + int((FACE_TOP + _ANVIL_BOT_Y) / 2 * scale)
        return QPoint(cx, cy)

    def _host_anvil_bounds(self) -> "QRect | None":
        """計算主砧 widget 的鐵砧範圍（螢幕座標），供重疊偵測使用。
        使用 FACE_L, FACE_R, FACE_TOP, _ANVIL_BOT_Y 四邊。"""
        host = self._host_widget_ref
        if host is None:
            return None
        try:
            scale = host.state.ui_scale
            left   = host.pos().x() + int(FACE_L * scale)
            right  = host.pos().x() + int(FACE_R * scale)
            top    = host.pos().y() + int(FACE_TOP * scale)
            bottom = host.pos().y() + int(_ANVIL_BOT_Y * scale)
            return QRect(left, top, right - left, bottom - top)
        except Exception:
            return None

    def _enter_coop_pending(self, new_pos: QPoint):
        """鐵砧中心進入主砧鐵砧範圍，切換為 pending 視覺。"""
        self._coop_mode = "pending"
        self._coop_flip_h_active = True
        # Y 鎖定：讓兩個 widget 的 FACE_TOP 在螢幕上對齊
        if self._host_widget_ref is not None:
            try:
                host_face_screen = (
                    self._host_widget_ref.pos().y()
                    + int(FACE_TOP * self._host_widget_ref.state.ui_scale)
                )
                my_face_offset = int(FACE_TOP * self._peer_state.ui_scale)
                self._coop_snap_y = host_face_screen - my_face_offset
            except Exception:
                self._coop_snap_y = new_pos.y()
        self.update()

    def _exit_coop_pending(self):
        """離開 pending 狀態（拖出鐵砧或取消邀請）。"""
        self._coop_mode = "none"
        self._coop_flip_h_active = False
        self._coop_overlap = False
        self._coop_snap_y = None
        self.update()

    def set_coop_active(self):
        """合作確認：視窗隱藏，交由主 widget 的 paintEvent 統一渲染鐵錘。"""
        self._coop_mode = "active"
        self._coop_flip_h_active = False  # 主 widget 用 transform 處理鏡像，這裡不需要
        self._coop_snap_y = None
        self.hide()   # 隱藏獨立視窗，合併到主 widget 渲染
        # 不呼叫 update()：已隱藏，更新無意義

    def set_coop_none(self):
        """結束合作，恢復正常獨立視窗顯示。"""
        self._coop_mode = "none"
        self._coop_flip_h_active = False
        self._coop_overlap = False
        self._coop_snap_y = None
        self.show()   # 重新顯示視窗
        self.update()

    def enterEvent(self, event):
        self._hover_hide_timer.stop()   # 取消待定的消失計時
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        # 不立即消失——等 400ms 讓玩家有時間移回 widget（與 ghost guide 一致）
        self._hover_hide_timer.start()
        super().leaveEvent(event)

    def _on_hover_hide(self):
        """400ms hover 消失 timer 觸發：清除 hover 狀態，刷新畫面。"""
        self._hovered = False
        self.update()

    # ── 右鍵選單 ──────────────────────────────────────────────────────────────

    def _show_context_menu(self, global_pos: QPoint):
        menu = QMenu(self)

        # ── 合作打鐵 ──────────────────────────────────────────────────────
        if self._coop_mode == "active":
            coop_end_act = QAction("🤝  結束合作打鐵", self)
            coop_end_act.triggered.connect(self._request_end_coop)
            menu.addAction(coop_end_act)
            menu.addSeparator()

        # 合作偏好（不論是否在合作中都可調整）
        pref = self._coop_pref_getter() if self._coop_pref_getter else None
        coop_pref_menu = menu.addMenu("🤝  合作偏好")
        for label, value in [("總是合作", True), ("總是拒絕", False), ("每次詢問", None)]:
            check = "✔  " if pref == value else "      "
            act = QAction(check + label, self)
            act.triggered.connect(lambda _c, v=value: self._set_coop_pref(v))
            coop_pref_menu.addAction(act)

        menu.addSeparator()

        mute_act = QAction("🔇  取消靜音" if self._muted else "🔇  靜音玩家", self)
        mute_act.triggered.connect(self._toggle_mute)
        menu.addAction(mute_act)

        name_act = QAction(
            "🏷  固定顯示名稱" if not self._name_visible else "🏷  取消固定顯示名稱", self)
        name_act.triggered.connect(self._toggle_name)
        menu.addAction(name_act)

        menu.addSeparator()

        # ── 視覺調整 submenu ──────────────────────────────────────────────
        visual_menu = menu.addMenu("🎨  視覺調整")

        anvil_act = QAction(
            "👁  顯示鐵砧" if self._peer_state.hide_anvil else "🫥  隱藏鐵砧", self)
        anvil_act.triggered.connect(self._toggle_hide_anvil)
        visual_menu.addAction(anvil_act)

        # 縮放（移入視覺調整）
        scale_menu = visual_menu.addMenu("🔍  縮放")
        for label, value in self._SCALE_OPTIONS:
            act = QAction(label, self)
            act.setCheckable(True)
            act.setChecked(abs(self._viewer_scale - value) < 1e-6)
            act.triggered.connect(lambda _checked, v=value: self._apply_scale(v))
            scale_menu.addAction(act)

        visual_menu.addSeparator()

        flip_h_act = QAction(("✔  " if self._flip_h else "      ") + "左右反轉", self)
        flip_h_act.triggered.connect(self._toggle_flip_h)
        visual_menu.addAction(flip_h_act)

        flip_v_act = QAction(("✔  " if self._flip_v else "      ") + "上下反轉", self)
        flip_v_act.triggered.connect(self._toggle_flip_v)
        visual_menu.addAction(flip_v_act)

        rot_label = f"↻  旋轉… ({int(self._rotation)}°)" if abs(self._rotation) > 0.5 else "↻  旋轉…"
        rotate_act = QAction(rot_label, self)
        rotate_act.triggered.connect(self._open_rotation_dialog)
        visual_menu.addAction(rotate_act)

        menu.addSeparator()

        top_act = QAction(
            "🔝  取消置頂" if self._always_on_top else "🔝  永遠置頂", self)
        top_act.triggered.connect(self._toggle_always_on_top)
        menu.addAction(top_act)

        center_act = QAction("📌  移動到螢幕中央", self)
        center_act.triggered.connect(self._move_to_center)
        menu.addAction(center_act)

        menu.exec_(global_pos)

    def _request_end_coop(self):
        """結束合作：回呼 BlacksmithWidget 處理。"""
        if self._coop_end_cb:
            self._coop_end_cb(self._player_name)

    def _set_coop_pref(self, value: "bool | None"):
        """設定對此玩家的合作偏好。"""
        if self._coop_pref_setter:
            self._coop_pref_setter(value)

    def _toggle_flip_h(self):
        self._flip_h = not self._flip_h

    def _toggle_flip_v(self):
        self._flip_v = not self._flip_v

    def _open_rotation_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("旋轉")
        dlg.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.Dialog)
        dlg.setFixedWidth(320)

        lay = QVBoxLayout(dlg)
        lay.setSpacing(10)
        lay.setContentsMargins(14, 14, 14, 14)

        angle_lbl = QLabel(f"{int(self._rotation)}°")
        angle_lbl.setAlignment(Qt.AlignCenter)
        angle_lbl.setStyleSheet("font-size: 20px; font-weight: bold;")
        lay.addWidget(angle_lbl)

        slider = QSlider(Qt.Horizontal)
        slider.setRange(-180, 180)
        slider.setValue(int(self._rotation))
        slider.setTickPosition(QSlider.TicksBothSides)
        slider.setTickInterval(45)
        lay.addWidget(slider)

        mark_row = QHBoxLayout()
        mark_row.addWidget(QLabel("-180°"))
        mark_row.addStretch()
        mark_row.addWidget(QLabel("0°"))
        mark_row.addStretch()
        mark_row.addWidget(QLabel("+180°"))
        lay.addLayout(mark_row)

        btn_row = QHBoxLayout()
        reset_btn = QPushButton("重置")
        reset_btn.clicked.connect(lambda: slider.setValue(0))
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        close_btn = QPushButton("關閉")
        close_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        def _on_change(val):
            angle_lbl.setText(f"{val}°")
            self._rotation = float(val)
            self.update()

        slider.valueChanged.connect(_on_change)
        dlg.exec_()

    def _toggle_always_on_top(self):
        self._always_on_top = not self._always_on_top
        if self._always_on_top:
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        else:
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool)
        self.show()   # setWindowFlags 後需重新 show 才生效

    def _toggle_mute(self):
        self._muted = not self._muted
        if self._muted:
            self._clear_bubble()

    def _toggle_hide_anvil(self):
        # 記錄本地覆蓋值，避免後續 frame 將鐵砧重新顯示
        self._viewer_hide_anvil = not self._peer_state.hide_anvil
        self._peer_state.hide_anvil = self._viewer_hide_anvil
        self.update()

    def _toggle_name(self):
        self._name_visible = not self._name_visible
        self.update()

    def _move_to_center(self):
        from PyQt5.QtWidgets import QApplication
        geo = QApplication.desktop().availableGeometry(self)
        self.move(geo.center() - self.rect().center())
        self.raise_()
        self.activateWindow()

    # ── 清理 ──────────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        self._loop.stop()
        self._bubble_timer.stop()
        self._bubble_fade_timer.stop()
        self._hover_hide_timer.stop()
        super().closeEvent(event)
