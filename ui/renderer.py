"""
Renderer — QPainter drawing for the blacksmith widget.
Background is fully transparent; only the anvil, hammer, sparks, and HUD are drawn.
All coordinates in 800×600 game space; ui_scale applied once at the top.

Performance notes:
  • QColor / QFont / QBrush / QPen objects pre-built at module level
    → eliminates ~50+ allocations per frame.
  • Static anvil polygons and wear-mark point pairs pre-built at module level
    → geometry that never changes is built once at import time.
  • cos_a / sin_a computed once per frame in draw_frame() and passed down
    → render_vcy_fast() avoids recomputing hammer_angle().
  • Spark sqrt called once per particle (was called twice).
  • charge_pulses dict mutation is now in-place (see state.py).
"""
import math
from PyQt5.QtGui  import QPainter, QColor, QPen, QBrush, QPolygonF, QFont
from PyQt5.QtCore import Qt, QPointF, QRectF

from config import (
    AX, AY_BASE, FACE_TOP, FACE_L, FACE_R,
    HEAD_OFFSET, HEAD_PERP,
    HL, HR, HP,
    GRIP_TO_BUTT,
    get_charge_color,
)
from game.state import GameState


# ── Pre-cached QColor constants (avoid per-frame allocation) ──────────────────

# Anvil (classic style)
_CA_SHADOW  = QColor(0,   0,   0,   128)
_CA_BASE    = QColor(33,  33,  33)
_CA_BASE2   = QColor(44,  44,  44)
_CA_WAIST   = QColor(38,  38,  38)
_CA_BODY    = QColor(46,  46,  46)
_CA_EDGE    = QColor(110, 110, 110)
_CA_HORN1   = QColor(37,  37,  37)
_CA_HORN2   = QColor(45,  45,  45)
_CA_HOLE    = QColor(8,   8,   8)
_CA_WEAR    = QColor(80,  80,  80,  102)
_CA_BEVEL   = QColor(56,  56,  56)

# Anvil v2 (icon style — lighter, sculptural silhouette)
_CA_V2_BODY  = QColor(92,  92,  95)    # face body trapezoid
_CA_V2_WAIST = QColor(60,  60,  62)    # narrow waist column (darkest — adds depth)
_CA_V2_BASE  = QColor(108, 108, 111)   # base platform
_CA_V2_HORN  = QColor(75,  75,  78)    # horn outer face
_CA_V2_HORN2 = QColor(100, 100, 102)   # horn inner highlight
_CA_V2_EDGE  = QColor(198, 198, 200)   # bright top-edge highlight
# V2 face surface base tint (blends toward strike_color on glow)
_V2_FACE_BASE = (148, 148, 151)

# Hammer
_CH_WOOD    = QColor(107, 58,  31)
_CH_GRAIN   = QColor(139, 84,  50)
_CH_GRIP    = QColor(58,  28,  10,  140)
_CH_BUTT    = QColor(74,  74,  74)
_CH_SHADOW  = QColor(17,  17,  17)
_CH_HEAD    = QColor(58,  58,  58)
_CH_BEVEL   = QColor(85,  85,  85)
_CH_POLL    = QColor(72,  72,  72)
_CH_FACE    = QColor(88,  88,  88)
_CH_COLLAR  = QColor(72,  72,  72)
_CH_COLLAR2 = QColor(96,  96,  96)

# HUD
_CHUD_HIT    = QColor(200, 200, 200)
_CHUD_FORCE  = QColor(255, 180,  60)
_CHUD_CLICK  = QColor(120, 210, 255)
_CHUD_SHADOW = QColor(0,   0,   0,   200)
_CHUD_ACTIVE = QColor(100, 230, 160)
_CHUD_IDLE   = QColor(100, 100, 110)
_CHUD_TURBO  = QColor(180, 130, 255)
_CHUD_FEVER  = QColor(255,  80, 255)
_CHUD_COOL   = QColor(140, 110, 175, 210)
_CHUD_STAR   = QColor(255, 200,  60)
_CHUD_BARBG  = QColor(15,  15,  15,  190)
_CHUD_BARBOR = QColor(80,  80,  80)

# ── Pre-cached QFont objects ──────────────────────────────────────────────────

_FONT_COUNTER    = QFont("Segoe UI", 15)
_FONT_METAL_NUM  = QFont("Segoe UI",  9)
_FONT_METAL_NUM.setBold(True)
_FONT_HIT_NUM_CRIT = QFont("Arial", 26)
_FONT_HIT_NUM_CRIT.setBold(True)
_FONT_MODE    = QFont("Segoe UI", 17)
_FONT_FEVER   = QFont("Arial", 26)
_FONT_FEVER.setBold(True)
_FONT_STAR    = QFont("Consolas", 13)
_FONT_HIT_NUM = QFont("Arial", 18)
_FONT_HIT_NUM.setBold(True)

# ── Pre-cached QBrush objects for fixed-color fills ───────────────────────────

_BR_CA_SHADOW  = QBrush(_CA_SHADOW)
_BR_CA_BASE    = QBrush(_CA_BASE)
_BR_CA_BASE2   = QBrush(_CA_BASE2)
_BR_CA_WAIST   = QBrush(_CA_WAIST)
_BR_CA_BODY    = QBrush(_CA_BODY)
_BR_CA_EDGE    = QBrush(_CA_EDGE)
_BR_CA_HORN1   = QBrush(_CA_HORN1)
_BR_CA_HORN2   = QBrush(_CA_HORN2)
_BR_CA_HOLE    = QBrush(_CA_HOLE)
_BR_CA_BEVEL   = QBrush(_CA_BEVEL)

# V2 brushes
_BR_V2_BODY  = QBrush(_CA_V2_BODY)
_BR_V2_WAIST = QBrush(_CA_V2_WAIST)
_BR_V2_BASE  = QBrush(_CA_V2_BASE)
_BR_V2_HORN  = QBrush(_CA_V2_HORN)
_BR_V2_HORN2 = QBrush(_CA_V2_HORN2)
_BR_V2_EDGE  = QBrush(_CA_V2_EDGE)
_BR_CH_WOOD    = QBrush(_CH_WOOD)
_BR_CH_GRAIN   = QBrush(_CH_GRAIN)
_BR_CH_GRIP    = QBrush(_CH_GRIP)
_BR_CH_BUTT    = QBrush(_CH_BUTT)
_BR_CH_SHADOW  = QBrush(_CH_SHADOW)
_BR_CH_HEAD    = QBrush(_CH_HEAD)
_BR_CH_BEVEL   = QBrush(_CH_BEVEL)
_BR_CH_POLL    = QBrush(_CH_POLL)
_BR_CH_FACE    = QBrush(_CH_FACE)
_BR_CH_COLLAR  = QBrush(_CH_COLLAR)
_BR_CH_COLLAR2 = QBrush(_CH_COLLAR2)

# ── Pre-cached QPen for text shadow ──────────────────────────────────────────

_PEN_TEXT_SHADOW = QPen(_CHUD_SHADOW)
_PEN_WEAR        = QPen(_CA_WEAR)
_PEN_WEAR.setWidthF(1)

# ── Anvil ghost guide (shown when hide_anvil + mouse on widget) ───────────────

def _make_ghost_pens():
    lp = QPen(QColor(210, 70, 20, 210))     # orange-red dashed line — visible on light bg
    lp.setWidthF(1.5)
    lp.setStyle(Qt.DashLine)
    cp = QPen(QColor(140, 200, 255, 200))   # sky-blue dashed circle
    cp.setWidthF(2.0)
    cp.setStyle(Qt.DashLine)
    xp = QPen(QColor(140, 200, 255, 180))   # crosshair inside circle
    xp.setWidthF(1.5)
    return lp, cp, xp

_GH_LINE_PEN, _GH_CIRC_PEN, _GH_CROSS_PEN = _make_ghost_pens()
_GH_DOT_BRUSH = QBrush(QColor(140, 200, 255, 220))
_GH_CIRC_R    = 22.0   # drag-handle circle radius (game units)
_GH_CROSS_LEN = 9.0    # crosshair arm length (game units)

# ── Pre-built static anvil geometry (never changes) ───────────────────────────

_POLY_HORN1 = QPolygonF([
    QPointF(FACE_R,      FACE_TOP + 10),
    QPointF(FACE_R,      FACE_TOP + 58),
    QPointF(FACE_R + 78, FACE_TOP + 32),
])
_POLY_HORN2 = QPolygonF([
    QPointF(FACE_R,      FACE_TOP + 10),
    QPointF(FACE_R,      FACE_TOP + 22),
    QPointF(FACE_R + 72, FACE_TOP + 30),
])

# ── V2 anvil geometry ─────────────────────────────────────────────────────────
_V2_FACE_BW     = 15               # left-side taper (px)
_V2_FACE_SHIFT  = 15               # face body shifted rightward (px)
_V2_FACE_R_EXP  = 55               # right side expands from waist-right upward by this (px)
_V2_FACE_BOT_Y  = FACE_TOP + 57    # 387 — face body bottom / waist top  (height = 45 px)
_V2_WAIST_HW    = 65               # waist half-width  (130 px wide)
_V2_WAIST_BOT_Y = _V2_FACE_BOT_Y + 32  # 419 — waist bottom / base top  (height = 32 px)
_V2_BASE_HW     = 90               # base half-width   (180 px wide)
_V2_BASE_H      = 38               # base height       (38 px)
_V2_BASE_BOT_Y  = _V2_WAIST_BOT_Y + _V2_BASE_H  # 470 — base bottom (for shadow)

# Face body corners — set directly by user
_V2_TL_X = 293   # top-left  x
_V2_TR_X = 508   # top-right x
_V2_BR_X = 468   # bot-right x
_V2_BL_X = 308   # bot-left  x
_V2_BASE_SHIFT = 8   # base shifted right (px)

# Metal width: starts narrow, expands to full anvil face width as quality increases
_METAL_W_START = 60.0

# ── Mode indicator geometry (V2 face-body centre) ─────────────────────────────
# 面體：top y = FACE_TOP+12 = 342，bottom y = _V2_FACE_BOT_Y = 387，height = 45
_MI_CX      = AX                                            # 390  砧中心 x
_MI_CY      = (_V2_FACE_BOT_Y + FACE_TOP + 12) // 2        # 364  面體中心 y
_MI_LINE_H  = int((_V2_FACE_BOT_Y - FACE_TOP - 12) * 0.84) # 37  豎線高度
_MI_LINE_Y0 = _MI_CY - _MI_LINE_H // 2                     # 346  豎線頂端
_MI_LINE_W  = 4.0                                           # px   豎線寬度
_MI_LINE_DX = 13                                            # px   豎線間距
_MI_CIRC_R  = int((_V2_FACE_BOT_Y - FACE_TOP - 12) * 0.24) # 10  蓄力圓半徑（縮小）
_MI_DOT_TR  = int((_V2_FACE_BOT_Y - FACE_TOP - 12) * 0.30) # 13  三角外接圓半徑
_MI_DOT_R   = 4.5                                           # px   小圓點半徑
# 三角點位：index 0=頂, 1=左下, 2=右下（逆時針順序）；整體下移 2px
_MI_DOT_POS = [
    (_MI_CX,                             _MI_CY - _MI_DOT_TR + 2),
    (_MI_CX - int(_MI_DOT_TR * 0.866),   _MI_CY + (_MI_DOT_TR + 1) // 2 + 2),
    (_MI_CX + int(_MI_DOT_TR * 0.866),   _MI_CY + (_MI_DOT_TR + 1) // 2 + 2),
]

_POLY_V2_FACE_BODY = QPolygonF([
    QPointF(_V2_TL_X, FACE_TOP + 12),   # 293, 342
    QPointF(_V2_TR_X, FACE_TOP + 12),   # 516, 342
    QPointF(_V2_BR_X, _V2_FACE_BOT_Y),  # 470, 387
    QPointF(_V2_BL_X, _V2_FACE_BOT_Y),  # 310, 387
])

# V2 HUD text Y anchors (text baseline, centred vertically in each zone)
_V2_HUD_HIT_Y   = int((FACE_TOP + 12 + _V2_FACE_BOT_Y)   / 2) + 7   # face body ≈ 371
_V2_HUD_FORCE_Y = int((_V2_FACE_BOT_Y + _V2_WAIST_BOT_Y) / 2) + 7   # waist      ≈ 410
_V2_HUD_CLICK_Y = int((_V2_WAIST_BOT_Y + _V2_BASE_BOT_Y) / 2) + 7   # base       ≈ 445

# Wear mark line pairs
_WEAR_LINES = [
    (QPointF(FACE_L + 10 + i * 28,      FACE_TOP + 1),
     QPointF(FACE_L + 10 + i * 28 + 16, FACE_TOP + 8))
    for i in range(7)
]

# Text-shadow offsets tuple (no list rebuild each call)
_SHADOW_OFS = ((-1, 0), (1, 0), (0, -1), (0, 1))


# ── Helper ────────────────────────────────────────────────────────────────────

def _poly(pts) -> QPolygonF:
    """Build a QPolygonF from an iterable of (x, y) tuples."""
    return QPolygonF([QPointF(x, y) for x, y in pts])


# ── Main entry ────────────────────────────────────────────────────────────────

def draw_frame(painter: QPainter, state: GameState):
    painter.save()
    painter.scale(state.ui_scale, state.ui_scale)

    # Compute trig once per frame — shared by _draw_hammer and render_vcy_fast
    a     = state.hammer_angle()
    cos_a = math.cos(a)
    sin_a = math.sin(a)

    # Fever / cooldown / star text — drawn first so anvil renders on top
    if not state.hide_anvil:
        _draw_turbo_overlay(painter, state)

    if not state.hide_anvil:
        if getattr(state, 'anvil_v2', True):
            _draw_anvil_v2(painter, state)
        else:
            _draw_anvil(painter, state)
        _draw_metal(painter, state)
        _draw_anvil_mode_indicator(painter, state)
    _draw_sparks(painter, state)
    _draw_hammer(painter, state, cos_a, sin_a)
    if not state.hide_anvil:
        _draw_flash(painter, state)
        _draw_hud(painter, state)
    else:
        if state.mouse_on_widget and not state.lock_position:
            _draw_anvil_ghost(painter, state)
    _draw_hit_numbers(painter, state)

    painter.restore()


# ── Anvil ─────────────────────────────────────────────────────────────────────

def _draw_anvil(painter: QPainter, state: GameState):
    # Feature 4: heat gives the glow a minimum floor so it fades slowly with heat.
    # Once heat_level reaches 0 the floor is 0 too — anvil fully cools.
    if state.show_heat_accum and state.heat_level > 0:
        glow = max(state.anvil_glow, state.heat_level * 0.22)
    else:
        glow = state.anvil_glow

    painter.setPen(Qt.NoPen)

    # Drop shadow
    painter.setBrush(_BR_CA_SHADOW)
    painter.drawEllipse(QPointF(AX, AY_BASE + 8), 155, 11)

    # Base platform
    painter.setBrush(_BR_CA_BASE)
    painter.drawRect(QRectF(AX - 128, AY_BASE - 42, 256, 42))
    painter.setBrush(_BR_CA_BASE2)
    painter.drawRect(QRectF(AX - 122, AY_BASE - 44, 244, 6))

    # Waist
    painter.setBrush(_BR_CA_WAIST)
    painter.drawRect(QRectF(AX - 72, AY_BASE - 132, 144, 90))

    # Face block body
    painter.setBrush(_BR_CA_BODY)
    painter.drawRect(QRectF(FACE_L, FACE_TOP + 10, FACE_R - FACE_L, 122))

    # Striking face surface — blends from dark grey toward the last strike colour
    sr, sg, sb = state.strike_color
    painter.setBrush(QBrush(QColor(
        int(70 + glow * (sr - 70)),
        int(70 + glow * (sg - 70)),
        int(70 + glow * (sb - 70)),
    )))
    painter.drawRect(QRectF(FACE_L, FACE_TOP, FACE_R - FACE_L, 12))

    # Face edge highlight
    painter.setBrush(_BR_CA_EDGE)
    painter.drawRect(QRectF(FACE_L + 2, FACE_TOP, FACE_R - FACE_L - 4, 3))

    # Horn — pre-built polygons
    painter.setBrush(_BR_CA_HORN1)
    painter.drawPolygon(_POLY_HORN1)
    painter.setBrush(_BR_CA_HORN2)
    painter.drawPolygon(_POLY_HORN2)

    # Hardy hole & Pritchel hole
    painter.setBrush(_BR_CA_HOLE)
    painter.drawRect(QRectF(AX - 44, FACE_TOP, 22, 11))
    painter.drawEllipse(QPointF(AX - 72, FACE_TOP + 5), 5, 5)

    # Wear marks — pre-built point pairs
    painter.setPen(_PEN_WEAR)
    for p1, p2 in _WEAR_LINES:
        painter.drawLine(p1, p2)

    # Right-side bevel
    painter.setPen(Qt.NoPen)
    painter.setBrush(_BR_CA_BEVEL)
    painter.drawRect(QRectF(FACE_R - 8, FACE_TOP + 12, 8, 120))

    # Anvil glow overlay
    if glow > 0.01:
        painter.setBrush(QBrush(QColor(255, 153, 0,   int(glow * 0.45 * 255))))
        painter.drawRect(QRectF(FACE_L, FACE_TOP, FACE_R - FACE_L, 12))
        painter.setBrush(QBrush(QColor(255, 255, 255, int(glow * 0.12 * 255))))
        painter.drawRect(QRectF(FACE_L, FACE_TOP, FACE_R - FACE_L, 12))


# ── Anvil v2 (icon style) ─────────────────────────────────────────────────────

def _draw_anvil_v2(painter: QPainter, state: GameState):
    """Icon-inspired anvil: trapezoidal face body, defined waist, rounded base.
    Striking surface stays at FACE_TOP (same as v1) — hammer alignment unchanged."""
    if state.show_heat_accum and state.heat_level > 0:
        glow = max(state.anvil_glow, state.heat_level * 0.22)
    else:
        glow = state.anvil_glow

    painter.setPen(Qt.NoPen)

    # Base (rounded corners, fixed height, shifted right)
    _bx = AX - _V2_BASE_HW + _V2_BASE_SHIFT
    painter.setBrush(_BR_V2_BASE)
    painter.drawRoundedRect(
        QRectF(_bx, _V2_WAIST_BOT_Y, _V2_BASE_HW * 2 - 8, _V2_BASE_H),
        10, 10,
    )
    # Base top-edge strip (lighter band for depth)
    painter.setBrush(_BR_CA_BASE2)
    painter.drawRoundedRect(
        QRectF(_bx + 5, _V2_WAIST_BOT_Y - 3, (_V2_BASE_HW - 5) * 2 - 8, 8),
        4, 4,
    )

    # Waist column (darkest part — visual depth between face and base)
    painter.setBrush(_BR_V2_WAIST)
    painter.drawRect(QRectF(
        AX - _V2_WAIST_HW, _V2_FACE_BOT_Y,
        _V2_WAIST_HW * 2,  _V2_WAIST_BOT_Y - _V2_FACE_BOT_Y,
    ))

    # Face body (trapezoid: wide at face, tapers to waist width)
    painter.setBrush(_BR_V2_BODY)
    painter.drawPolygon(_POLY_V2_FACE_BODY)

    # Striking face surface — blends from v2 base tint toward last strike colour
    fr, fg, fb = _V2_FACE_BASE
    sr, sg, sb = state.strike_color
    painter.setBrush(QBrush(QColor(
        int(fr + glow * (sr - fr)),
        int(fg + glow * (sg - fg)),
        int(fb + glow * (sb - fb)),
    )))
    painter.drawRect(QRectF(_V2_TL_X, FACE_TOP, _V2_TR_X - _V2_TL_X, 12))

    # Top-edge highlight (bright strip)
    painter.setBrush(_BR_V2_EDGE)
    painter.drawRect(QRectF(_V2_TL_X + 2, FACE_TOP, _V2_TR_X - _V2_TL_X - 4, 3))

    # Wear marks
    painter.setPen(_PEN_WEAR)
    for p1, p2 in _WEAR_LINES:
        painter.drawLine(p1, p2)

    # Glow overlay
    painter.setPen(Qt.NoPen)
    if glow > 0.01:
        painter.setBrush(QBrush(QColor(255, 153, 0,   int(glow * 0.45 * 255))))
        painter.drawRect(QRectF(_V2_TL_X, FACE_TOP, _V2_TR_X - _V2_TL_X, 12))
        painter.setBrush(QBrush(QColor(255, 255, 255, int(glow * 0.12 * 255))))
        painter.drawRect(QRectF(_V2_TL_X, FACE_TOP, _V2_TR_X - _V2_TL_X, 12))


# ── Metal piece ───────────────────────────────────────────────────────────────

def _draw_metal(painter: QPainter, state: GameState):
    """Draw the current metal piece sitting on the anvil face."""
    if not getattr(state, 'show_metal_forge', True):
        return
    m = getattr(state, 'current_metal', None)
    if m is None or m.dead:
        return

    # Spawn scale-in animation
    spawn_scale = min(1.0, m.spawn_t)
    if spawn_scale <= 0.01:
        return

    # Completion flash: pulse bright then fade out
    if m.flash_t > 0.0:
        # Brief white-hot pulse (0→0.3), then fade (0.3→1.0)
        if m.flash_t < 0.3:
            brightness = m.flash_t / 0.3          # 0→1
            alpha = 255
        else:
            brightness = 0.0
            alpha = int((1.0 - (m.flash_t - 0.3) / 0.7) * 255)
        alpha = max(0, alpha)
    else:
        brightness = 0.0
        alpha = 255

    # Geometry — centred on AX, width matches v2 face, sits on FACE_TOP
    r, g, b   = m.color
    # Mix toward white for the brightness pulse
    r = min(255, int(r + brightness * (255 - r)))
    g = min(255, int(g + brightness * (255 - g)))
    b = min(255, int(b + brightness * (255 - b)))

    thickness = m.thickness * spawn_scale
    # Width expands from _METAL_W_START toward full anvil face width as quality grows
    metal_w   = (_METAL_W_START + ((_V2_TR_X - _V2_TL_X) - _METAL_W_START) * m.ratio) * spawn_scale
    mx        = AX - metal_w / 2
    my        = FACE_TOP - thickness

    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(QColor(r, g, b, alpha)))
    painter.drawRoundedRect(QRectF(mx, my, metal_w, thickness), 3, 3)

    # Subtle top-edge highlight (brighter strip)
    hl_h = max(2.0, thickness * 0.15)
    painter.setBrush(QBrush(QColor(
        min(255, r + 40), min(255, g + 40), min(255, b + 40), alpha)))
    painter.drawRoundedRect(QRectF(mx + 2, my, metal_w - 4, hl_h), 2, 2)

    # 數字標籤已移除；金屬類型改以顏色區分（熱色→冷色插值）


# ── Hammer ────────────────────────────────────────────────────────────────────

def _draw_hammer(painter: QPainter, state: GameState, cos_a: float, sin_a: float):
    # render_vcy_fast reuses the already-computed cos_a / sin_a
    rvcy = _render_vcy_fast(state, cos_a, sin_a)
    vcx  = state.vcx

    # Local rotate helper — defined once per call, captures cos_a / sin_a / vcx / rvcy
    def p(along, perp):
        return (vcx  + along * cos_a + perp * sin_a,
                rvcy + along * sin_a - perp * cos_a)

    cf_now = (state.typing_charge / max(1, state.typing_max_charge)
              if state.kb_mode in ("charge", "charge_legacy") else 0.0)

    painter.setPen(Qt.NoPen)

    # ── Charge pulse rings ─────────────────────────────────────────────────
    for pulse in state.charge_pulses:
        t  = pulse["t"]
        m  = t * 20
        al = (1 - t) ** 1.4 * 0.9
        if al < 0.015:
            continue
        col = get_charge_color(pulse["cf"])
        pen = QPen(QColor(col[0], col[1], col[2], int(al * 255)))
        pen.setWidthF(max(0.5, 2.2 - t * 1.4))
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPolygon(_poly([
            p(HL - m, -(HP + m)), p(HR + m, -(HP + m)),
            p(HR + m,  (HP + m)), p(HL - m,  (HP + m)),
        ]))
        painter.setPen(Qt.NoPen)

    # ── Handle ────────────────────────────────────────────────────────────
    painter.setBrush(_BR_CH_WOOD)
    painter.drawPolygon(_poly([
        p(-GRIP_TO_BUTT, -6), p(-GRIP_TO_BUTT, 6), p(HL, 6), p(HL, -6),
    ]))
    painter.setBrush(_BR_CH_GRAIN)
    painter.drawPolygon(_poly([
        p(-GRIP_TO_BUTT + 3, -4), p(-GRIP_TO_BUTT + 3, -1),
        p(HL - 2, -1),            p(HL - 2, -4),
    ]))

    # Grip wraps
    painter.setBrush(_BR_CH_GRIP)
    for al in range(5, 36, 12):
        painter.drawPolygon(_poly([
            p(al, -6), p(al + 5, -6), p(al + 5, 6), p(al, 6),
        ]))

    # Butt cap
    painter.setBrush(_BR_CH_BUTT)
    painter.drawPolygon(_poly([
        p(-GRIP_TO_BUTT - 3, -7), p(-GRIP_TO_BUTT + 2, -7),
        p(-GRIP_TO_BUTT + 2,  7), p(-GRIP_TO_BUTT - 3,  7),
    ]))

    # ── Head ──────────────────────────────────────────────────────────────
    painter.setBrush(_BR_CH_SHADOW)
    painter.drawPolygon(_poly([
        p(HL+2, -HP+2), p(HR+2, -HP+2), p(HR+2, HP+2), p(HL+2, HP+2),
    ]))
    painter.setBrush(_BR_CH_HEAD)
    painter.drawPolygon(_poly([p(HL, -HP), p(HR, -HP), p(HR, HP), p(HL, HP)]))
    painter.setBrush(_BR_CH_BEVEL)
    painter.drawPolygon(_poly([p(HL,-HP), p(HR,-HP), p(HR-2,-HP+5), p(HL+2,-HP+5)]))
    painter.setBrush(_BR_CH_POLL)
    painter.drawPolygon(_poly([p(HR,-HP), p(HR,HP), p(HR-4,HP-2), p(HR-4,-HP+2)]))
    painter.setBrush(_BR_CH_FACE)
    painter.drawPolygon(_poly([p(HL,HP-5), p(HR,HP-5), p(HR,HP), p(HL,HP)]))
    painter.setBrush(_BR_CH_COLLAR)
    painter.drawPolygon(_poly([p(HL-2,-HP-2), p(HL+5,-HP-2), p(HL+5,HP+2), p(HL-2,HP+2)]))
    painter.setBrush(_BR_CH_COLLAR2)
    painter.drawPolygon(_poly([p(HL-2,-HP-2), p(HL+5,-HP-2), p(HL+5,-HP+4), p(HL-2,-HP+4)]))

    # ── Proximity heat glow ────────────────────────────────────────────────
    prox = max(0.0, 1.0 - max(0.0, FACE_TOP - state.vcy) / 120.0)
    if prox > 0.01:
        painter.setBrush(QBrush(QColor(255, 102, 0, int(prox * 0.6 * 255))))
        painter.drawPolygon(_poly([p(HL,HP-6), p(HR,HP-6), p(HR,HP), p(HL,HP)]))
        if prox > 0.5:
            painter.setBrush(QBrush(QColor(255, 255, 255, int((prox - 0.5) * 0.3 * 255))))
            painter.drawPolygon(_poly([p(HL,HP-3), p(HR,HP-3), p(HR,HP), p(HL,HP)]))

    # ── Charge fill / border ───────────────────────────────────────────────
    if cf_now > 0:
        col = get_charge_color(cf_now)
        gm  = 3 + cf_now * 5
        painter.setBrush(QBrush(QColor(col[0], col[1], col[2],
                                       int((0.08 + cf_now * 0.20) * 255))))
        painter.drawPolygon(_poly([
            p(HL-gm, -(HP+gm)), p(HR+gm, -(HP+gm)),
            p(HR+gm,  (HP+gm)), p(HL-gm,  (HP+gm)),
        ]))
        painter.setBrush(QBrush(QColor(col[0], col[1], col[2],
                                       int((0.18 + cf_now * 0.60) * 255))))
        painter.drawPolygon(_poly([p(HL,-HP), p(HR,-HP), p(HR,HP), p(HL,HP)]))
        pen2 = QPen(QColor(col[0], col[1], col[2],
                           int((0.75 + cf_now * 0.22) * 255)))
        pen2.setWidthF(1.5)
        painter.setPen(pen2)
        painter.setBrush(Qt.NoBrush)
        painter.drawPolygon(_poly([p(HL,-HP), p(HR,-HP), p(HR,HP), p(HL,HP)]))
        painter.setPen(Qt.NoPen)


def _render_vcy_fast(state: GameState, cos_a: float, sin_a: float) -> float:
    """Clamp vcy so the hammer face doesn't visually penetrate the anvil or metal.
    Reuses already-computed cos_a / sin_a from draw_frame — avoids redundant trig."""
    face_y = state.vcy + HEAD_OFFSET * sin_a - HEAD_PERP * cos_a
    face_x = state.vcx + HEAD_OFFSET * cos_a + HEAD_PERP * sin_a
    # Visual surface rises by metal thickness when metal is visible and fully spawned
    m = getattr(state, 'current_metal', None)
    if (getattr(state, 'show_metal_forge', True)
            and not state.hide_anvil and m is not None and not m.dead
            and m.spawn_t >= 1.0 and m.flash_t <= 0.0):
        visual_top = FACE_TOP - m.thickness
    else:
        visual_top = FACE_TOP
    if face_y > visual_top and FACE_L - 20 <= face_x <= FACE_R + 20:
        return visual_top - HEAD_OFFSET * sin_a + HEAD_PERP * cos_a
    return state.vcy


# ── Sparks ────────────────────────────────────────────────────────────────────

def _draw_sparks(painter: QPainter, state: GameState):
    for s in state.sparks:
        al = s.frac
        sz = s.size * al
        if sz < 0.4:
            continue

        r, g, b = s.color
        spd2    = s.vx * s.vx + s.vy * s.vy

        # Streak tail for fast sparks — sqrt called only once
        if sz > 0.8 and spd2 > 12000:
            spd = math.sqrt(spd2)
            tl  = spd * 0.03674          # 0.0167 * 2.2, pre-computed
            pen = QPen(QColor(r, g, b, int(al * 0.4 * 255)))
            pen.setWidthF(max(0.5, sz * 0.55))
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.drawLine(
                QPointF(s.x, s.y),
                QPointF(s.x - s.vx / spd * tl, s.y - s.vy / spd * tl),
            )

        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(r, g, b, int(al * 255))))
        painter.drawRect(QRectF(s.x - sz * 0.5, s.y - sz * 0.5, sz, sz))


# ── Strike flash ──────────────────────────────────────────────────────────────

def _draw_flash(painter: QPainter, state: GameState):
    if not getattr(state, 'show_strike_pulse', True):
        return
    sf = state.strike_flash
    if sf < 0.004:
        return
    # Flash anchored to the actual hit surface (metal top or anvil face)
    hit_y  = getattr(state, 'last_hit_surface_y', float(FACE_TOP))
    m      = getattr(state, 'current_metal', None)
    if (getattr(state, 'show_metal_forge', True)
            and not state.hide_anvil and m is not None and not m.dead
            and m.spawn_t >= 1.0 and m.flash_t <= 0.0):
        fl = AX - (_V2_TR_X - _V2_TL_X) / 2
        fw = float(_V2_TR_X - _V2_TL_X)
    else:
        fl = float(FACE_L)
        fw = float(FACE_R - FACE_L)
    spread = 60 * sf
    sr, sg, sb = state.strike_color
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(QColor(sr, sg, sb, int(sf * 200))))
    painter.drawRect(QRectF(
        fl - spread, hit_y - spread * 0.5,
        fw + spread * 2, 18 + spread,
    ))
    # Crit bonus flash — extra bright gold + white burst
    if getattr(state, 'last_crit', False):
        painter.setBrush(QBrush(QColor(255, 230, 50, int(sf * 130))))
        painter.drawRect(QRectF(
            fl - spread * 1.6, hit_y - spread * 0.9,
            fw + spread * 3.2, 22 + spread * 1.6,
        ))
        painter.setBrush(QBrush(QColor(255, 255, 255, int(sf * 90))))
        painter.drawRect(QRectF(
            fl - spread * 0.5, hit_y - spread * 0.25,
            fw + spread, 12 + spread * 0.5,
        ))


# ── Hit number popups (Feature 2) ────────────────────────────────────────────

def _draw_hit_numbers(painter: QPainter, state: GameState):
    """Draw floating "+N" numbers that rise from the anvil face after each hit.
    Critical hits use a larger gold font."""
    if not state.show_hit_numbers or not state.hit_numbers:
        return
    for hn in state.hit_numbers:
        t     = hn["age"] / hn["max_age"]            # 0 → 1
        alpha = int((1.0 - t ** 1.6) * 255)
        if alpha < 4:
            continue
        is_crit = hn.get("crit", False)
        text = f"+{hn['value']}"
        if is_crit:
            painter.setFont(_FONT_HIT_NUM_CRIT)
            r, g, b = 255, 230, 50   # gold
        else:
            painter.setFont(_FONT_HIT_NUM)
            r, g, b = hn["color"]
        fm = painter.fontMetrics()
        tx = hn["x"] - fm.horizontalAdvance(text) / 2
        ty = hn["y"]

        # ── 暴擊放射光芒（在文字之前繪製，文字壓在上面）─────────────────
        if is_crit:
            t_burst = max(0.0, 1.0 - t * 2.5)   # 前 40% 時間內有光芒
            if t_burst > 0.02:
                burst_alpha = int(alpha * t_burst * 0.75)
                star_cx = hn["x"]
                star_cy = ty - fm.ascent() * 0.45
                ray_pen = QPen(QColor(255, 230, 50, burst_alpha))
                ray_pen.setWidthF(1.5)
                painter.setPen(ray_pen)
                for i in range(8):
                    angle  = i * (math.pi / 4)
                    rl     = 22 if i % 2 == 0 else 13   # 長短交替
                    ca, sa = math.cos(angle), math.sin(angle)
                    painter.drawLine(
                        QPointF(star_cx + ca * 16,        star_cy + sa * 16),
                        QPointF(star_cx + ca * (16 + rl), star_cy + sa * (16 + rl)),
                    )
                painter.setPen(Qt.NoPen)

        # Shadow (slightly thicker for crit)
        painter.setPen(QPen(QColor(0, 0, 0, min(255, alpha))))
        shadow_ofs = ((-2, 0), (2, 0), (0, -2), (0, 2)) if is_crit else _SHADOW_OFS
        for ox, oy in shadow_ofs:
            painter.drawText(QPointF(tx + ox, ty + oy), text)
        # Text
        painter.setPen(QPen(QColor(r, g, b, alpha)))
        painter.drawText(QPointF(tx, ty), text)


# ── HUD ───────────────────────────────────────────────────────────────────────

def _draw_hud(painter: QPainter, state: GameState):
    # ── Counter lines ─────────────────────────────────────────────────────
    if getattr(state, 'anvil_v2', True):
        # V2: each counter sits inside its own anvil zone, symbol + number only
        _v2_ctrs = [
            (state.show_hit,   f"⚒ {state.hit_count}",   _V2_HUD_HIT_Y,   _CHUD_HIT),
            (state.show_force, f"◈ {state.force_count}",  _V2_HUD_FORCE_Y, _CHUD_FORCE),
            (state.show_click, f"✦ {state.click_count}",  _V2_HUD_CLICK_Y, _CHUD_CLICK),
        ]
        painter.setFont(_FONT_COUNTER)
        fm = painter.fontMetrics()
        for show, text, game_y, col in _v2_ctrs:
            if not show:
                continue
            tx = AX - fm.horizontalAdvance(text) / 2
            painter.setPen(_PEN_TEXT_SHADOW)
            for ox, oy in _SHADOW_OFS:
                painter.drawText(QPointF(tx + ox, game_y + oy), text)
            painter.setPen(QPen(col))
            painter.drawText(QPointF(tx, game_y), text)
    else:
        # V1: classic stacked layout with labels
        _all = [
            (state.show_hit,   f"⚒ 打擊  {state.hit_count}",   362, _CHUD_HIT),
            (state.show_force, f"◈ 力道  {state.force_count}",  390, _CHUD_FORCE),
            (state.show_click, f"✦ 點擊  {state.click_count}",  418, _CHUD_CLICK),
        ]
        lines = [(text, y, col) for show, text, y, col in _all if show]
        if lines:
            painter.setFont(_FONT_COUNTER)
            fm = painter.fontMetrics()
            for text, game_y, col in lines:
                tx = AX - fm.horizontalAdvance(text) / 2
                painter.setPen(_PEN_TEXT_SHADOW)
                for ox, oy in _SHADOW_OFS:
                    painter.drawText(QPointF(tx + ox, game_y + oy), text)
                painter.setPen(QPen(col))
                painter.drawText(QPointF(tx, game_y), text)

    # ── Charge bar ────────────────────────────────────────────────────────
    if state.show_charge_bar and state.kb_mode in ("charge", "charge_legacy") and state.kb_active:
        cf = state.typing_charge / max(1, state.typing_max_charge)
        bx = float(FACE_L + 10)
        by = float(FACE_TOP + 1)
        bw = float(FACE_R - FACE_L - 20)
        bh = 8.0

        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(_CHUD_BARBG))
        painter.drawRect(QRectF(bx, by, bw, bh))

        if cf > 0:
            col = get_charge_color(cf)
            painter.setBrush(QBrush(QColor(col[0], col[1], col[2])))
            painter.drawRect(QRectF(bx, by, bw * cf, bh))

        pen3 = QPen(_CHUD_BARBOR)
        pen3.setWidthF(1)
        painter.setPen(pen3)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(QRectF(bx, by, bw, bh))



# ── Anvil mode indicators (drawn AFTER metal, ON TOP of anvil face) ──────────

def _draw_anvil_mode_indicator(painter: QPainter, state: GameState):
    """根據目前模式在砧頭面體中央繪製指示器（僅 V2 砧）。"""
    if not getattr(state, 'anvil_v2', True):
        return
    glow = state.anvil_glow
    sr, sg, sb = state.strike_color
    if state.turbo_mode:
        _draw_turbo_lines(painter, state, glow, sr, sg, sb)
    elif state.kb_mode in ("charge", "charge_legacy"):
        _draw_charge_circle(painter, state, glow, sr, sg, sb)
    else:
        _draw_combo_dots(painter, state, glow, sr, sg, sb)


def _draw_turbo_lines(painter: QPainter, state: GameState,
                      glow: float, sr: int, sg: int, sb: int):
    """渦輪模式：三條豎線作為充能槽。
    冷卻中 → 從底部填充；充能完畢 → 持續亮金；Fever → 脈動粉紫；每次打擊短暫閃爍。"""
    painter.setPen(Qt.NoPen)
    lw   = _MI_LINE_W
    y0   = float(_MI_LINE_Y0)
    ht   = float(_MI_LINE_H)
    t    = state.play_time
    line_xs = (_MI_CX - _MI_LINE_DX, _MI_CX, _MI_CX + _MI_LINE_DX)

    if state.fever_active:
        # Fever：粉紫脈動，活躍直線（turbo_line_idx）更亮，模仿連打三角點輪換
        pulse      = 0.55 + 0.45 * abs(math.sin(t * 5.0))
        active_ln  = getattr(state, 'turbo_line_idx', -1)  # -1 = fever 尚未打擊
        for idx, lx in enumerate(line_xs):
            is_active = (active_ln >= 0 and idx == active_ln)
            # 活躍線：全亮；非活躍線（已有亮線時）：降至 45% 亮度
            dim = 1.0 if (is_active or active_ln < 0) else 0.45
            fr  = min(255, int(255 * pulse * dim))
            fg  = int(55 * pulse * dim)
            fb  = min(255, int(220 * pulse * dim))
            # 打擊閃光疊加（活躍線接受更多 strike_color）
            mix = 1.0 if is_active else 0.5
            fr  = min(255, int(fr + glow * max(0, sr - fr) * mix))
            fg  = min(255, int(fg + glow * max(0, sg - fg) * mix))
            fb  = min(255, int(fb + glow * max(0, sb - fb) * mix))
            fa  = int((200 if is_active else 140 if active_ln >= 0 else 200) * pulse)
            # 活躍線打擊縮小光暈（矩形版，對應連打點的光圈縮小效果）
            if is_active and glow > 0.04:
                exp_w = glow * 7.0
                exp_h = glow * 5.0
                painter.setBrush(QBrush(QColor(fr, fg, fb, int(glow * 160))))
                painter.drawRoundedRect(
                    QRectF(lx - lw/2 - exp_w, y0 - exp_h,
                           lw + exp_w * 2,    ht + exp_h * 2),
                    2.5, 2.5,
                )
            painter.setBrush(QBrush(QColor(fr, fg, fb, fa)))
            painter.drawRoundedRect(QRectF(lx - lw/2, y0, lw, ht), 1.5, 1.5)

    elif state.fever_cooldown_timer > 0:
        # 充能中：深色底 + 岩漿由底部湧上（深橙紅→亮橙黃）
        cd_total = max(1.0, state.fever_cooldown_duration)
        prog     = 1.0 - state.fever_cooldown_timer / cd_total
        fill_h   = ht * prog
        fill_r   = min(255, int(200 + prog * 55))   # 200→255  始終熾熱
        fill_g   = min(255, int(45  + prog * 155))  # 45→200   漸趨橙黃
        # 岩漿內部亮芯（越滿越亮）
        pulse    = 0.80 + 0.20 * abs(math.sin(t * (2.5 + prog * 4.0)))
        fill_a   = int((160 + prog * 90) * pulse)   # 遠比之前亮
        for lx in line_xs:
            rx = lx - lw / 2
            # 深色槽底
            painter.setBrush(QBrush(QColor(20, 18, 18, 215)))
            painter.drawRoundedRect(QRectF(rx, y0, lw, ht), 1.5, 1.5)
            if fill_h > 0.5:
                # 岩漿填充層
                painter.setBrush(QBrush(QColor(fill_r, fill_g, 0, fill_a)))
                painter.drawRoundedRect(
                    QRectF(rx, y0 + ht - fill_h, lw, fill_h), 1.5, 1.5
                )
                # 頂端亮邊（模擬熾熱液面）
                edge_h = max(1.5, lw * 0.5)
                painter.setBrush(QBrush(QColor(255, min(255, fill_g + 60), 40,
                                               min(255, int(fill_a * 1.25)))))
                painter.drawRoundedRect(
                    QRectF(rx, y0 + ht - fill_h, lw, edge_h), 1.5, 1.5
                )
            if glow > 0.05:   # 打擊閃光
                painter.setBrush(QBrush(QColor(sr, sg, sb, int(glow * 150))))
                painter.drawRoundedRect(QRectF(rx, y0, lw, ht), 1.5, 1.5)

    else:
        # 充能滿（待機）：明顯呼吸金光，振幅大、頻率略快
        pulse = 0.42 + 0.58 * abs(math.sin(t * 2.5))   # 42%→100%，非常明顯
        for lx in line_xs:
            fr = min(255, int(230 + glow * max(0, sr - 230)))
            fg = min(255, int(150 + glow * max(0, sg - 150)))
            fb = min(255, int( 20 + glow * max(0, sb -  20)))
            fa = int((190 + glow * 65) * pulse)
            painter.setBrush(QBrush(QColor(fr, fg, fb, fa)))
            painter.drawRoundedRect(QRectF(lx - lw/2, y0, lw, ht), 1.5, 1.5)


def _draw_charge_circle(painter: QPainter, state: GameState,
                        glow: float, sr: int, sg: int, sb: int):
    """蓄力模式：砧頭中央圓形凹槽，打擊後依段數閃出對應顏色的光。"""
    cx, cy = float(_MI_CX), float(_MI_CY)
    rc     = float(_MI_CIRC_R)
    painter.setPen(Qt.NoPen)
    # 深色凹槽底
    painter.setBrush(QBrush(QColor(12, 12, 12, 225)))
    painter.drawEllipse(QPointF(cx, cy), rc, rc)
    # 打擊後閃光
    if glow > 0.01:
        painter.setBrush(QBrush(QColor(sr, sg, sb, int(glow * 215))))
        painter.drawEllipse(QPointF(cx, cy), rc, rc)
    # 凹槽輪廓
    rim = QPen(QColor(72, 68, 64, 185))
    rim.setWidthF(1.5)
    painter.setPen(rim)
    painter.setBrush(Qt.NoBrush)
    painter.drawEllipse(QPointF(cx, cy), rc + 0.75, rc + 0.75)
    painter.setPen(Qt.NoPen)


def _draw_combo_dots(painter: QPainter, state: GameState,
                     glow: float, sr: int, sg: int, sb: int):
    """連打模式：三個小圓構成正三角，平時全暗，打擊後活躍點出現品紅縮小光圈+藍色閃光。"""
    painter.setPen(Qt.NoPen)
    active = getattr(state, 'combo_dot_idx', -1)   # -1 = 尚未打擊，無效果
    for i, (dx, dy) in enumerate(_MI_DOT_POS):
        # 打擊縮小光圈：鮮亮藍（混入 strike_color）
        if i == active and glow > 0.04:
            glow_r = _MI_DOT_R + 3.0 + glow * 5.5
            painter.setBrush(QBrush(QColor(
                min(255, int(80  + glow * max(0, sr - 80))),
                min(255, int(170 + glow * max(0, sg - 170))),
                255,
                int(glow * 200),
            )))
            painter.drawEllipse(QPointF(dx, dy), glow_r, glow_r)
        # 點本體：活躍點打擊時短暫亮藍（隨 glow 衰退），其餘始終暗色
        if i == active and glow > 0.04:
            r = min(255, int(22 + (180 - 22) * glow))   # 暗→亮天藍
            g = min(255, int(22 + (230 - 22) * glow))
            b = min(255, int(25 + (255 - 25) * glow))
            a = min(255, int(215 + 40        * glow))
        else:
            r = min(255, int(22 + glow * (sr - 22) * 0.28))
            g = min(255, int(22 + glow * (sg - 22) * 0.28))
            b = min(255, int(25 + glow * (sb - 25) * 0.32))
            a = 215
        painter.setBrush(QBrush(QColor(r, g, b, a)))
        painter.drawEllipse(QPointF(dx, dy), _MI_DOT_R, _MI_DOT_R)


# ── Turbo / Fever overlay (drawn BEFORE anvil so anvil stays on top) ─────────

def _draw_turbo_overlay(painter: QPainter, state: GameState):
    """Fever text, cooldown text, and charge-star display.
    Called before the anvil so the anvil always renders on top."""
    if not state.turbo_mode:
        return

    if state.fever_active:
        pulse      = 0.7 + 0.3 * abs(math.sin(state.fever_timer * 4.0))
        fever_text = f"Fever!  {int(state.fever_timer)}s"
        painter.setFont(_FONT_FEVER)
        fm_f = painter.fontMetrics()
        tx   = AX - fm_f.horizontalAdvance(fever_text) / 2
        ty   = FACE_TOP - 28
        painter.setPen(QPen(QColor(255, 50, 210, int(pulse * 150))))
        for ox, oy in ((-3, 0), (3, 0), (0, -3), (0, 3)):
            painter.drawText(QPointF(tx + ox, ty + oy), fever_text)
        painter.setPen(QPen(QColor(255, int(210 * pulse), int(40 + 200 * (1.0 - pulse)))))
        painter.drawText(QPointF(tx, ty), fever_text)

    elif state.fever_cooldown_timer > 0:
        pass   # 充能條改在 _draw_hud 中繪製（anvil 之後，避免被金屬塊蓋住）

    elif state.consecutive_full_charge > 0:
        filled  = "★" * state.consecutive_full_charge
        empty   = "☆" * max(0, state.fever_threshold - state.consecutive_full_charge)
        cc_text = filled + empty
        painter.setFont(_FONT_STAR)
        tw_cc = painter.fontMetrics().horizontalAdvance(cc_text)
        painter.setPen(QPen(_CHUD_STAR))
        painter.drawText(QPointF(AX - tw_cc / 2, FACE_TOP - 12), cc_text)


# ── Anvil ghost guide ─────────────────────────────────────────────────────────

def _draw_anvil_ghost(painter: QPainter, state: GameState):
    """Drawn when hide_anvil=True and the mouse is on the widget.
    Shows a dashed horizontal line at the anvil face level and a dashed
    drag-handle circle at the strike point so the user can reposition the widget."""
    # ── Invisible hit area ────────────────────────────────────────────────
    # Windows per-pixel hit testing routes clicks ONLY to pixels with alpha > 0.
    # The ghost guide sits on a fully transparent background, so without this
    # rect the circle would never receive mouse events.  alpha=2 is invisible.
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(QColor(0, 0, 0, 2)))
    painter.drawRect(QRectF(
        FACE_L - 55,
        FACE_TOP - _GH_CIRC_R - 6,
        FACE_R - FACE_L + 110,
        _GH_CIRC_R * 2 + 12,
    ))

    # ── Horizontal face line ──────────────────────────────────────────────
    painter.setPen(_GH_LINE_PEN)
    painter.drawLine(QPointF(FACE_L - 50, FACE_TOP), QPointF(FACE_R + 50, FACE_TOP))

    # ── Drag-handle circle at strike point (AX, FACE_TOP) ────────────────
    painter.setPen(_GH_CIRC_PEN)
    painter.setBrush(Qt.NoBrush)
    painter.drawEllipse(QPointF(AX, FACE_TOP), _GH_CIRC_R, _GH_CIRC_R)

    # ── Crosshair inside circle ───────────────────────────────────────────
    painter.setPen(_GH_CROSS_PEN)
    painter.drawLine(
        QPointF(AX - _GH_CROSS_LEN, FACE_TOP),
        QPointF(AX + _GH_CROSS_LEN, FACE_TOP),
    )
    painter.drawLine(
        QPointF(AX, FACE_TOP - _GH_CROSS_LEN),
        QPointF(AX, FACE_TOP + _GH_CROSS_LEN),
    )

    # ── Centre dot ───────────────────────────────────────────────────────
    painter.setPen(Qt.NoPen)
    painter.setBrush(_GH_DOT_BRUSH)
    painter.drawEllipse(QPointF(AX, FACE_TOP), 3.0, 3.0)
