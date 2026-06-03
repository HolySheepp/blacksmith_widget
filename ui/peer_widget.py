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

from PyQt5.QtWidgets import QWidget, QMenu, QAction
from PyQt5.QtCore    import Qt, QTimer, QPoint, QRect, QRectF
from PyQt5.QtGui     import (QPainter, QColor, QPen, QBrush, QFont,
                             QPainterPath)

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

_LOOP_MS = 50              # 20 fps
_LOOP_S  = _LOOP_MS / 1000.0

_BUBBLE_HOLD_MS  = 5000    # 氣泡完整顯示時間
_BUBBLE_FADE_MS  = 100     # 淡出計時器間隔
_BUBBLE_FADE_STEP = 0.08   # 每次淡出降低的 alpha

_DRAG_THRESH2 = 25


# ── Peer 顯示狀態（模仿 GameState，供 renderer 使用）────────────────────────

class PeerDisplayState:
    """模仿 GameState 的介面，供 ui.renderer.draw_frame 使用。

    renderer 大量以 getattr(state, attr, default) 讀取屬性，因此這裡只需提供
    繪製所需的欄位即可。"""

    def __init__(self, ui_scale: float = _BASE_SCALE):
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
        self.show_metal_forge = False
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
        self.vcx = float(data.get("vcx", self.vcx))
        self.vcy = float(data.get("vcy", self.vcy))
        self.vcvx = float(data.get("vcvx", 0.0))
        self.vcvy = float(data.get("vcvy", 0.0))
        self.has_hit = bool(data.get("has_hit", self.has_hit))
        self.anvil_glow = float(data.get("anvil_glow", self.anvil_glow))
        sc = data.get("strike_color")
        if isinstance(sc, list) and len(sc) == 3:
            self.strike_color = tuple(sc)
        self.hide_anvil = bool(data.get("hide_anvil", self.hide_anvil))
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
        sx, sy = float(AX), float(FACE_TOP)
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
        """每幀更新（sparks 衰減等）。"""
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

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)

        # ── 縮放 ──────────────────────────────────────────────────────────
        self._viewer_scale = 1.0
        self._peer_state = PeerDisplayState(ui_scale=_BASE_SCALE * self._viewer_scale)
        self._apply_scale(self._viewer_scale)

        # ── 設定 ──────────────────────────────────────────────────────────
        self._muted = False
        self._name_visible = True
        self._hovered = False

        # ── 拖曳狀態 ──────────────────────────────────────────────────────
        self._press_global: QPoint | None = None
        self._drag_offset:  QPoint | None = None
        self._is_dragging:  bool          = False

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

        # ── 遊戲迴圈 ──────────────────────────────────────────────────────
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
        self._peer_state.tick(_LOOP_S)
        self.update()

    # ── 公開 API ──────────────────────────────────────────────────────────────

    def update_from_frame(self, data: dict):
        """由 widget.py 在收到 frame 時呼叫。"""
        self._peer_state.update_from_frame(data)
        self.update()

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
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 1. 鐵鎚 + 鐵砧場景（draw_frame 內部會 save/scale/restore）
        draw_frame(painter, self._peer_state)

        # 2. 玩家名稱（widget 座標）
        painter.save()
        painter.resetTransform()
        self._draw_name(painter)
        painter.restore()

        # 3. 聊天氣泡（widget 座標）
        painter.save()
        painter.resetTransform()
        self._draw_bubble(painter)
        painter.restore()

        painter.end()

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
        cx = self.width() / 2
        ty = self.height() - 8   # 底部中央

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
            self.move(event.globalPos() - self._drag_offset)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._is_dragging  = False
            self._press_global = None
            self._drag_offset  = None

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    # ── 右鍵選單 ──────────────────────────────────────────────────────────────

    def _show_context_menu(self, global_pos: QPoint):
        menu = QMenu(self)

        mute_act = QAction("🔇  取消靜音" if self._muted else "🔇  靜音玩家", self)
        mute_act.triggered.connect(self._toggle_mute)
        menu.addAction(mute_act)

        anvil_act = QAction(
            "👁  顯示鐵砧" if self._peer_state.hide_anvil else "🫥  隱藏鐵砧", self)
        anvil_act.triggered.connect(self._toggle_hide_anvil)
        menu.addAction(anvil_act)

        name_act = QAction(
            "🏷  顯示名稱" if not self._name_visible else "🏷  隱藏名稱", self)
        name_act.triggered.connect(self._toggle_name)
        menu.addAction(name_act)

        menu.addSeparator()

        # 縮放子選單
        scale_menu = menu.addMenu("🔍  縮放")
        for label, value in self._SCALE_OPTIONS:
            act = QAction(label, self)
            act.setCheckable(True)
            act.setChecked(abs(self._viewer_scale - value) < 1e-6)
            act.triggered.connect(lambda _checked, v=value: self._apply_scale(v))
            scale_menu.addAction(act)

        menu.addSeparator()

        center_act = QAction("📌  移動到螢幕中央", self)
        center_act.triggered.connect(self._move_to_center)
        menu.addAction(center_act)

        menu.exec_(global_pos)

    def _toggle_mute(self):
        self._muted = not self._muted
        if self._muted:
            self._clear_bubble()

    def _toggle_hide_anvil(self):
        self._peer_state.hide_anvil = not self._peer_state.hide_anvil
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
        super().closeEvent(event)
