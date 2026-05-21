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

# Anvil
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

_FONT_COUNTER = QFont("Consolas", 14)
_FONT_MODE    = QFont("Consolas", 10)
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

    _draw_anvil(painter, state)
    _draw_sparks(painter, state)
    _draw_hammer(painter, state, cos_a, sin_a)
    _draw_flash(painter, state)
    _draw_hit_numbers(painter, state)
    _draw_hud(painter, state)

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
    """Like state.render_vcy() but reuses already-computed cos_a / sin_a."""
    face_y = state.vcy + HEAD_OFFSET * sin_a - HEAD_PERP * cos_a
    face_x = state.vcx + HEAD_OFFSET * cos_a + HEAD_PERP * sin_a
    if face_y > FACE_TOP and FACE_L - 20 <= face_x <= FACE_R + 20:
        return FACE_TOP - HEAD_OFFSET * sin_a + HEAD_PERP * cos_a
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
    sf = state.strike_flash
    if sf < 0.004:
        return
    spread = 60 * sf
    sr, sg, sb = state.strike_color
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(QColor(sr, sg, sb, int(sf * 200))))
    painter.drawRect(QRectF(
        FACE_L - spread, FACE_TOP - spread * 0.5,
        (FACE_R - FACE_L) + spread * 2, 18 + spread,
    ))


# ── Hit number popups (Feature 2) ────────────────────────────────────────────

def _draw_hit_numbers(painter: QPainter, state: GameState):
    """Draw floating "+N" numbers that rise from the anvil face after each hit."""
    if not state.show_hit_numbers or not state.hit_numbers:
        return
    painter.setFont(_FONT_HIT_NUM)
    fm = painter.fontMetrics()
    for hn in state.hit_numbers:
        t     = hn["age"] / hn["max_age"]            # 0 → 1
        alpha = int((1.0 - t ** 1.6) * 255)
        if alpha < 4:
            continue
        text = f"+{hn['value']}"
        r, g, b = hn["color"]
        tx = hn["x"] - fm.horizontalAdvance(text) / 2
        ty = hn["y"]
        # Shadow
        painter.setPen(QPen(QColor(0, 0, 0, min(255, alpha))))
        for ox, oy in _SHADOW_OFS:
            painter.drawText(QPointF(tx + ox, ty + oy), text)
        # Text
        painter.setPen(QPen(QColor(r, g, b, alpha)))
        painter.drawText(QPointF(tx, ty), text)


# ── HUD ───────────────────────────────────────────────────────────────────────

def _draw_hud(painter: QPainter, state: GameState):
    # ── Counter lines ─────────────────────────────────────────────────────
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

    # ── Mode indicator ────────────────────────────────────────────────────
    if state.turbo_mode:
        mode_text = "渦輪"
        if state.fever_active:
            mode_col = _CHUD_FEVER
        elif state.fever_cooldown_timer > 0:
            mode_col = _CHUD_COOL
        else:
            mode_col = _CHUD_TURBO
    else:
        if state.kb_mode == "charge":
            mode_text = "蓄力"
        elif state.kb_mode == "charge_legacy":
            mode_text = "蓄力舊"
        else:
            mode_text = "連打"
        mode_col = _CHUD_ACTIVE if state.kb_active else _CHUD_IDLE

    painter.setFont(_FONT_MODE)
    painter.setPen(QPen(mode_col))
    painter.drawText(QPointF(FACE_R - 62, 444), mode_text)

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

    # ── Turbo / Fever overlay ─────────────────────────────────────────────
    if not state.turbo_mode:
        return

    if state.fever_active:
        pulse      = 0.7 + 0.3 * abs(math.sin(state.fever_timer * 4.0))
        fever_text = f"Fever!  {int(state.fever_timer)}s"
        painter.setFont(_FONT_FEVER)
        fm_f = painter.fontMetrics()
        tx   = AX - fm_f.horizontalAdvance(fever_text) / 2
        ty   = FACE_TOP - 50
        painter.setPen(QPen(QColor(255, 50, 210, int(pulse * 150))))
        for ox, oy in ((-3, 0), (3, 0), (0, -3), (0, 3)):
            painter.drawText(QPointF(tx + ox, ty + oy), fever_text)
        painter.setPen(QPen(QColor(255, int(210 * pulse), int(40 + 200 * (1.0 - pulse)))))
        painter.drawText(QPointF(tx, ty), fever_text)

    elif state.fever_cooldown_timer > 0:
        cd_text = f"冷卻中  {int(state.fever_cooldown_timer)}s"
        painter.setFont(_FONT_MODE)
        tw_cd = painter.fontMetrics().horizontalAdvance(cd_text)
        painter.setPen(QPen(_CHUD_COOL))
        painter.drawText(QPointF(AX - tw_cd / 2, FACE_TOP - 12), cd_text)

    elif state.consecutive_full_charge > 0:
        filled  = "★" * state.consecutive_full_charge
        empty   = "☆" * max(0, state.fever_threshold - state.consecutive_full_charge)
        cc_text = filled + empty
        painter.setFont(_FONT_STAR)
        tw_cc = painter.fontMetrics().horizontalAdvance(cc_text)
        painter.setPen(QPen(_CHUD_STAR))
        painter.drawText(QPointF(AX - tw_cc / 2, FACE_TOP - 12), cc_text)
