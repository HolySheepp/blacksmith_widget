"""game/skin_registry.py — Single source of truth for all equippable skins.

To add a new skin, add ONE _register() call at the bottom of this file.
Everything else (chest drop pools, skin picker list, toast labels) auto-updates.

Fields per skin:
  skin_id    : str          — unique key used throughout the codebase
  label      : str          — display name (UI + toast notifications)
  slot       : str          — "anvil" or "hammer"
  chest_tier : int | None   — 0 = wood, 1 = iron, 2 = gold, None = not droppable
  draw_thumb : callable(painter, w, h)    — skin-picker thumbnail
  draw_game  : callable(painter, state)   — in-game replacement shape (optional)
               None  → renderer uses its built-in drawing logic (colour variants)
               fn    → renderer calls this function instead (fully custom shape)

PyQt5 is only imported lazily inside draw functions, so game-logic code that
imports this module (e.g. chest.py) never triggers Qt at import time.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Optional


# ── SkinDef ───────────────────────────────────────────────────────────────────

@dataclass
class SkinDef:
    skin_id:       str
    label:         str
    slot:          str                  # "anvil" or "hammer"
    chest_tier:    Optional[int]        # 0 / 1 / 2 / None
    draw_thumb:    Callable             # (painter, w, h) → None
    draw_game:     Optional[Callable]   # (painter, state) → None
    # Per-skin particle / material overrides (anvil slot only):
    draw_spark:    Optional[Callable]   # (painter, spark)  → None  replaces spark dot
    draw_ember:    Optional[Callable]   # (painter, ember)  → None  replaces ember dot
    draw_material: Optional[Callable]   # (painter, state)  → None  replaces metal block


SKIN_REGISTRY: dict[str, SkinDef] = {}  # insertion-ordered (Python 3.7+)


def _register(skin_id: str, label: str, slot: str,
              chest_tier: Optional[int],
              draw_thumb: Callable,
              draw_game:     Optional[Callable] = None,
              draw_spark:    Optional[Callable] = None,
              draw_ember:    Optional[Callable] = None,
              draw_material: Optional[Callable] = None):
    SKIN_REGISTRY[skin_id] = SkinDef(
        skin_id, label, slot, chest_tier, draw_thumb,
        draw_game, draw_spark, draw_ember, draw_material)


# ── Thumbnail factories for colour-variant skins ──────────────────────────────
# These use the actual game renderer (lazy-imported) so the thumbnail is always
# pixel-perfect and auto-syncs when the renderer changes.

_SCALE       = 0.6
_GAME_W, _GAME_H = 800, 600
_ANVIL_CROP  = (168, 191, 145, 90)   # (x, y, w, h) crop from 480×360 render
_HAMMER_CROP = (244,  32,  64, 84)
_HAMMER_VCY  = 148                   # game-coord vcy for idle-pose thumbnail


def _anvil_thumb(active_anvil_skin: str | None) -> Callable:
    """Return draw_thumb that renders the anvil with the given skin colour."""
    _skin = active_anvil_skin

    def draw(painter, w, h):
        import math
        from types import SimpleNamespace
        from PyQt5.QtGui import QPixmap, QPainter as _P
        from PyQt5.QtCore import Qt, QRect
        from ui.renderer import _draw_anvil_v2

        state = SimpleNamespace(
            heat_level=0, anvil_glow=0.0,
            strike_color=(90, 90, 90),
            active_anvil_skin=_skin,
            hide_anvil=False,
        )
        pw, ph = int(_GAME_W * _SCALE), int(_GAME_H * _SCALE)
        pix = QPixmap(pw, ph)
        pix.fill(Qt.transparent)
        p2 = _P(pix)
        p2.setRenderHint(_P.Antialiasing)
        p2.scale(_SCALE, _SCALE)
        _draw_anvil_v2(p2, state)
        p2.end()

        cx, cy, cw, ch = _ANVIL_CROP
        cropped = pix.copy(QRect(cx, cy, cw, ch))
        scaled  = cropped.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        painter.drawPixmap((w - scaled.width())  // 2,
                           (h - scaled.height()) // 2, scaled)
    return draw


def _hammer_thumb(active_hammer_skin: str | None) -> Callable:
    """Return draw_thumb that renders the hammer with the given skin colour."""
    _skin = active_hammer_skin

    def draw(painter, w, h):
        import math
        from types import SimpleNamespace
        from PyQt5.QtGui import QPixmap, QPainter as _P
        from PyQt5.QtCore import Qt, QRect
        from config import KB_X, IDLE_ANGLE
        from ui.renderer import _draw_hammer

        state = SimpleNamespace(
            vcx=float(KB_X), vcy=float(_HAMMER_VCY),
            kb_mode="normal", typing_charge=0, typing_max_charge=5,
            charge_pulses=[], active_hammer_skin=_skin,
            play_time=0.0, hide_anvil=False,
            current_metal=None, current_chest=None, show_metal_forge=True,
        )
        pw, ph = int(_GAME_W * _SCALE), int(_GAME_H * _SCALE)
        pix = QPixmap(pw, ph)
        pix.fill(Qt.transparent)
        p2 = _P(pix)
        p2.setRenderHint(_P.Antialiasing)
        p2.scale(_SCALE, _SCALE)
        cos_a = math.cos(IDLE_ANGLE)
        sin_a = math.sin(IDLE_ANGLE)
        _draw_hammer(p2, state, cos_a, sin_a)
        p2.end()

        cx, cy, cw, ch = _HAMMER_CROP
        cropped = pix.copy(QRect(cx, cy, cw, ch))
        scaled  = cropped.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        painter.drawPixmap((w - scaled.width())  // 2,
                           (h - scaled.height()) // 2, scaled)
    return draw


# ══════════════════════════════════════════════════════════════════════════════
# Skin registrations
# ── Add new skins here ────────────────────────────────────────────────────────
# Order determines display order in the skin picker.
#
# Colour-variant skins: draw_game=None (renderer handles via active_*_skin).
# Custom-shape skins:   provide draw_game(painter, state) for in-game rendering.
# ══════════════════════════════════════════════════════════════════════════════

# ── Anvil skins ───────────────────────────────────────────────────────────────
_register("anvil_default", "預設鐵砧", "anvil", None, _anvil_thumb(None))
_register("anvil_wood",    "木鐵砧",   "anvil", 0,    _anvil_thumb("anvil_wood"))
_register("anvil_silver",  "銀鐵砧",   "anvil", 1,    _anvil_thumb("anvil_silver"))
_register("anvil_gold",    "金鐵砧",   "anvil", 2,    _anvil_thumb("anvil_gold"))

# ── Hammer skins ──────────────────────────────────────────────────────────────
_register("hammer_default", "預設錘子", "hammer", None, _hammer_thumb(None))
_register("hammer_wood",    "木錘",     "hammer", 0,    _hammer_thumb("hammer_wood"))
_register("hammer_silver",  "銀錘",     "hammer", 1,    _hammer_thumb("hammer_silver"))
_register("hammer_gold",    "金錘",     "hammer", 2,    _hammer_thumb("hammer_gold"))

# ══════════════════════════════════════════════════════════════════════════════
# 木魚 (Wooden Fish) — anvil slot  +  木魚棍 (Mallet) — hammer slot
# Both use draw_game for fully custom shapes.
# 木魚 also overrides sparks → 煩惱粒子, embers → 功德光點, metal → 業力方塊.
# ══════════════════════════════════════════════════════════════════════════════

# ── In-game draw functions ────────────────────────────────────────────────────

def _woodfish_game(painter, state):
    """Draw wooden fish (木魚) replacing the anvil.
    Asymmetric pear/fish silhouette: rounder LEFT side (head), tapered RIGHT (tail).
    Top of fish = FACE_TOP = 330 (the hammer strike point). No heat glow."""
    from PyQt5.QtGui import QColor, QBrush, QPen, QPainterPath
    from PyQt5.QtCore import Qt, QRectF
    from config import AX, FACE_TOP

    cx    = float(AX)        # 390
    top_y = float(FACE_TOP)  # 330
    bh    = 132.0
    cy    = top_y + bh * 0.50   # vertical centre ≈ 396

    # ── Pear / fish silhouette (QPainterPath) ─────────────────────────────────
    # Left side (head): half-width ≈ 112 → rounder, larger bezier arcs
    # Right side (tail): half-width ≈ 88  → slightly tapered
    path = QPainterPath()
    path.moveTo(cx, top_y)
    path.cubicTo(cx + 32,  top_y + 2,  cx + 88, cy - 36,  cx + 88, cy)
    path.cubicTo(cx + 88,  cy + 36,    cx + 30, top_y + bh, cx - 5, top_y + bh)
    path.cubicTo(cx - 50,  top_y + bh, cx - 112, cy + 46,  cx - 112, cy)
    path.cubicTo(cx - 112, cy - 46,    cx - 50, top_y + 2,  cx,      top_y)
    path.closeSubpath()

    # Slightly inset inner path (leaves a thin dark rim all around)
    inner = QPainterPath()
    inner.moveTo(cx, top_y + 4)
    inner.cubicTo(cx + 26, top_y + 5,   cx + 82, cy - 32,  cx + 82, cy)
    inner.cubicTo(cx + 82, cy + 32,     cx + 24, top_y + bh - 4, cx - 5, top_y + bh - 4)
    inner.cubicTo(cx - 46, top_y + bh - 4, cx - 106, cy + 42, cx - 106, cy)
    inner.cubicTo(cx - 106, cy - 42,    cx - 46, top_y + 5,  cx, top_y + 4)
    inner.closeSubpath()

    painter.setPen(Qt.NoPen)

    # 1. Ground shadow
    painter.setBrush(QBrush(QColor(0, 0, 0, 50)))
    painter.drawEllipse(QRectF(cx - 100, top_y + bh - 4, 195, 18))

    # 2. Dark rim / underside layer (full silhouette)
    painter.setBrush(QBrush(QColor(110, 75, 32)))
    painter.drawPath(path)

    # 3. Main warm wood body
    painter.setBrush(QBrush(QColor(168, 128, 62)))
    painter.drawPath(inner)

    # 4. Upper-left highlight dome
    painter.setBrush(QBrush(QColor(195, 158, 82, 170)))
    painter.drawEllipse(QRectF(cx - 96, top_y + 10, 108, 56))

    # 5. Small specular spot
    painter.setBrush(QBrush(QColor(228, 200, 122, 115)))
    painter.drawEllipse(QRectF(cx - 80, top_y + 13, 50, 26))

    # 6. Wood grain rings (horizontal arcs, shifted left to follow head curve)
    painter.setPen(QPen(QColor(128, 90, 36, 95), 1.4))
    painter.setBrush(Qt.NoBrush)
    for frac, hw in [(0.32, 86), (0.48, 94), (0.62, 84), (0.75, 66)]:
        gy = top_y + bh * frac
        x0 = cx - hw * 0.95
        painter.drawArc(QRectF(x0, gy - 5, hw * 1.9, 10), 0, 180 * 16)
    painter.setPen(Qt.NoPen)

    # 7. Slit — rim then dark opening (offset left so it sits over the head area)
    sw, sh = 122.0, 15.0
    sx     = cx - 14          # shifted toward rounder left side
    slit_y = top_y + bh * 0.34
    painter.setBrush(QBrush(QColor(85, 55, 20)))
    painter.drawEllipse(QRectF(sx - sw/2 - 3, slit_y - sh/2 - 3, sw + 6, sh + 6))
    painter.setBrush(QBrush(QColor(20, 9, 2)))
    painter.drawEllipse(QRectF(sx - sw/2, slit_y - sh/2, sw, sh))

    # 8. Outer edge highlight line
    painter.setPen(QPen(QColor(215, 175, 95, 130), 1.8))
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(path)
    painter.setPen(Qt.NoPen)
    # ── No heat / hot-metal glow — wood doesn't get red-hot ──────────────────


# ── 木魚 theme overrides ──────────────────────────────────────────────────────

def _wf_draw_spark(painter, spark):
    """Scattered '煩惱' (troubles) particles — dark purple smoke puffs."""
    from PyQt5.QtGui import QColor, QBrush
    from PyQt5.QtCore import Qt, QPointF
    al = spark.frac
    sz = spark.size * al * 0.9
    if sz < 0.35:
        return
    painter.setPen(Qt.NoPen)
    # Outer puff
    painter.setBrush(QBrush(QColor(72, 52, 100, int(al * 195))))
    painter.drawEllipse(QPointF(spark.x, spark.y), sz * 0.65, sz * 0.65)
    # Lighter core
    if sz > 1.2:
        painter.setBrush(QBrush(QColor(125, 95, 155, int(al * 120))))
        painter.drawEllipse(QPointF(spark.x, spark.y), sz * 0.28, sz * 0.28)


def _wf_draw_ember(painter, ember):
    """Rising '功德' (merit) light particles — warm gold with white core."""
    from PyQt5.QtGui import QColor, QBrush
    from PyQt5.QtCore import Qt, QPointF
    frac = ember.frac
    if frac > 0.85:
        af = (1.0 - frac) / 0.15
    elif frac < 0.45:
        af = frac / 0.45
    else:
        af = 1.0
    alpha = int(af * 158)
    if alpha < 4:
        return
    sz = ember.size
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(QColor(255, 212, 65, alpha)))
    painter.drawEllipse(QPointF(ember.x, ember.y), sz, sz)
    if alpha > 30:
        painter.setBrush(QBrush(QColor(255, 252, 210, int(alpha * 0.62))))
        painter.drawEllipse(QPointF(ember.x, ember.y), sz * 0.40, sz * 0.40)


def _wf_draw_material(painter, state):
    """'業力方塊' — dark karma block with a troubled face, replacing the metal bar.
    Size / flash animation mirrors the original metal block logic exactly."""
    from PyQt5.QtGui import QColor, QBrush, QPen
    from PyQt5.QtCore import Qt, QRectF, QPointF
    from config import AX, FACE_TOP

    m = getattr(state, 'current_metal', None)
    if m is None or m.dead:
        return
    spawn_scale = min(1.0, m.spawn_t)
    if spawn_scale <= 0.01:
        return

    if m.flash_t > 0.0:
        brightness = (m.flash_t / 0.3) if m.flash_t < 0.3 else 0.0
        alpha      = 255 if m.flash_t < 0.3 else max(
            0, int((1.0 - (m.flash_t - 0.3) / 0.7) * 255))
    else:
        brightness, alpha = 0.0, 255

    _FULL_W   = 215.0                   # mirrors _V2_TR_X - _V2_TL_X in renderer
    thickness = m.thickness * spawn_scale
    block_w   = (60.0 + (_FULL_W - 60.0) * m.ratio) * spawn_scale
    mx = AX - block_w / 2
    my = FACE_TOP - thickness

    # Colour: dark charcoal → deep indigo as quality/ratio rises
    br = int(44 + m.ratio * 58)
    bg = int(36 + m.ratio * 30)
    bb = int(54 + m.ratio * 68)
    if brightness > 0:
        br = min(255, int(br + brightness * (255 - br)))
        bg = min(255, int(bg + brightness * (255 - bg)))
        bb = min(255, int(bb + brightness * (255 - bb)))

    painter.setPen(Qt.NoPen)

    # Block body
    painter.setBrush(QBrush(QColor(br, bg, bb, alpha)))
    painter.drawRoundedRect(QRectF(mx, my, block_w, thickness), 4, 4)

    # Top-edge highlight
    hl_h = max(2.0, thickness * 0.18)
    painter.setBrush(QBrush(QColor(
        min(255, br + 42), min(255, bg + 34), min(255, bb + 58), alpha)))
    painter.drawRoundedRect(QRectF(mx + 2, my, block_w - 4, hl_h), 2, 2)

    # Troubled face — visible once the block is wide/tall enough
    if block_w >= 56 and thickness >= 14 and alpha > 55:
        face_cx = float(AX)
        face_cy = my + thickness * 0.50
        eye_col = QColor(min(255, br + 92), min(255, bg + 78),
                         min(255, bb + 92), int(alpha * 0.80))
        eye_r   = max(1.5, thickness * 0.065)
        spread  = min(block_w * 0.13, 17.0)

        # Dot eyes
        painter.setBrush(QBrush(eye_col))
        painter.drawEllipse(
            QPointF(face_cx - spread, face_cy - thickness * 0.06), eye_r, eye_r)
        painter.drawEllipse(
            QPointF(face_cx + spread, face_cy - thickness * 0.06), eye_r, eye_r)

        # Frown (bottom arc: startAngle=0, spanAngle=-180*16 = clockwise through 6 o'clock)
        frown_w = min(block_w * 0.20, 22.0)
        frown_y = face_cy + thickness * 0.14
        pen = QPen(eye_col, max(1.3, thickness * 0.068))
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawArc(
            QRectF(face_cx - frown_w / 2, frown_y - frown_w * 0.28,
                   frown_w, frown_w * 0.55),
            0, -180 * 16)
        painter.setPen(Qt.NoPen)


def _mallet_game(painter, state):
    """Draw a wooden mallet (木魚棍) replacing the hammer.
    Ball centre at p(HEAD_OFFSET, 0), radius = HEAD_PERP — bottom of ball
    lands at p(HEAD_OFFSET, HEAD_PERP), matching _render_vcy_fast clamping."""
    import math
    from PyQt5.QtGui import QColor, QBrush, QPen
    from PyQt5.QtCore import Qt, QPointF
    from config import (HEAD_OFFSET, HEAD_PERP, GRIP_TO_BUTT, HL,
                        FACE_TOP, get_charge_color)
    from ui.renderer import _poly, _render_vcy_fast

    a     = state.hammer_angle()
    cos_a = math.cos(a)
    sin_a = math.sin(a)
    rvcy  = _render_vcy_fast(state, cos_a, sin_a)
    vcx   = state.vcx

    def p(along, perp):
        return (vcx  + along * cos_a + perp * sin_a,
                rvcy + along * sin_a - perp * cos_a)

    ball_r    = float(HEAD_PERP)          # 30 — bottom of ball = strike face
    stick_end = HEAD_OFFSET - ball_r + 5  # stick overlaps ball base slightly

    painter.setPen(Qt.NoPen)

    # ── Charge pulse rings (same shape as ball) ───────────────────────────────
    for pulse in state.charge_pulses:
        t  = pulse["t"]
        m  = t * 20
        al = (1 - t) ** 1.4 * 0.9
        if al < 0.015:
            continue
        col = get_charge_color(pulse["cf"])
        bx, by = p(HEAD_OFFSET, 0)
        r_ring  = ball_r + m
        pen = QPen(QColor(col[0], col[1], col[2], int(al * 255)))
        pen.setWidthF(max(0.5, 2.2 - t * 1.4))
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QPointF(bx, by), r_ring, r_ring)
        painter.setPen(Qt.NoPen)

    # ── Handle (stick) ────────────────────────────────────────────────────────
    # Dark base layer
    painter.setBrush(QBrush(QColor(100, 60, 20)))
    painter.drawPolygon(_poly([
        p(-GRIP_TO_BUTT, -6), p(-GRIP_TO_BUTT, 6),
        p(stick_end,      6), p(stick_end,     -6),
    ]))
    # Light top highlight
    painter.setBrush(QBrush(QColor(148, 95, 38)))
    painter.drawPolygon(_poly([
        p(-GRIP_TO_BUTT, -6), p(-GRIP_TO_BUTT, -2),
        p(stick_end,     -2), p(stick_end,     -6),
    ]))
    # Grain highlight strip
    painter.setBrush(QBrush(QColor(165, 108, 50)))
    painter.drawPolygon(_poly([
        p(-GRIP_TO_BUTT + 4, -4), p(-GRIP_TO_BUTT + 4, -1),
        p(stick_end - 2,     -1), p(stick_end - 2,     -4),
    ]))

    # Grip wraps (dark leather)
    painter.setBrush(QBrush(QColor(58, 28, 10, 140)))
    for al in range(5, 36, 12):
        painter.drawPolygon(_poly([
            p(al, -6), p(al + 5, -6), p(al + 5, 6), p(al, 6),
        ]))

    # Butt cap
    painter.setBrush(QBrush(QColor(74, 74, 74)))
    painter.drawPolygon(_poly([
        p(-GRIP_TO_BUTT - 3, -7), p(-GRIP_TO_BUTT + 2, -7),
        p(-GRIP_TO_BUTT + 2,  7), p(-GRIP_TO_BUTT - 3,  7),
    ]))

    # ── Ball ──────────────────────────────────────────────────────────────────
    bx, by = p(HEAD_OFFSET, 0)

    # Drop shadow
    painter.setBrush(QBrush(QColor(0, 0, 0, 55)))
    painter.drawEllipse(QPointF(bx + 3, by + 3), ball_r, ball_r)

    # Dark underside
    painter.setBrush(QBrush(QColor(75, 44, 14)))
    painter.drawEllipse(QPointF(bx, by), ball_r, ball_r)

    # Main ball colour
    painter.setBrush(QBrush(QColor(118, 74, 26)))
    painter.drawEllipse(QPointF(bx - 2, by - 2), ball_r * 0.90, ball_r * 0.90)

    # Top-left specular highlight
    painter.setBrush(QBrush(QColor(175, 118, 50, 160)))
    painter.drawEllipse(QPointF(bx - ball_r * 0.28, by - ball_r * 0.28),
                        ball_r * 0.38, ball_r * 0.34)

    # Outer edge line
    painter.setPen(QPen(QColor(155, 100, 42, 180), 1.5))
    painter.setBrush(Qt.NoBrush)
    painter.drawEllipse(QPointF(bx, by), ball_r, ball_r)
    painter.setPen(Qt.NoPen)

    # ── Proximity heat glow on ball ───────────────────────────────────────────
    prox = max(0.0, 1.0 - max(0.0, FACE_TOP - state.vcy) / 120.0)
    if prox > 0.01:
        painter.setBrush(QBrush(QColor(255, 102, 0, int(prox * 0.55 * 255))))
        painter.drawEllipse(QPointF(bx, by), ball_r, ball_r)
        if prox > 0.5:
            painter.setBrush(QBrush(
                QColor(255, 255, 255, int((prox - 0.5) * 0.28 * 255))))
            painter.drawEllipse(QPointF(bx, by), ball_r * 0.6, ball_r * 0.6)

    # ── Charge fill on ball ───────────────────────────────────────────────────
    cf = (state.typing_charge / max(1, state.typing_max_charge)
          if state.kb_mode == "charge" else 0.0)
    if cf > 0:
        col = get_charge_color(cf)
        gm  = cf * 6
        painter.setBrush(QBrush(QColor(col[0], col[1], col[2],
                                       int((0.08 + cf * 0.20) * 255))))
        painter.drawEllipse(QPointF(bx, by), ball_r + gm, ball_r + gm)
        painter.setBrush(QBrush(QColor(col[0], col[1], col[2],
                                       int((0.18 + cf * 0.60) * 255))))
        painter.drawEllipse(QPointF(bx, by), ball_r, ball_r)
        pen2 = QPen(QColor(col[0], col[1], col[2],
                           int((0.75 + cf * 0.22) * 255)))
        pen2.setWidthF(1.5)
        painter.setPen(pen2)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QPointF(bx, by), ball_r, ball_r)
        painter.setPen(Qt.NoPen)


# ── Thumbnail factories (offscreen render → crop → scale) ─────────────────────

_WOODFISH_CROP = (155, 192, 144, 90)   # (x, y, w, h) in 480×360 widget coords — pear shape, left-wide


def _woodfish_thumb_fn() -> Callable:
    def draw(painter, w, h):
        from types import SimpleNamespace
        from PyQt5.QtGui import QPixmap, QPainter as _P
        from PyQt5.QtCore import Qt, QRect
        state = SimpleNamespace(
            heat_level=0, anvil_glow=0.0,
            strike_color=(90, 90, 90),
            active_anvil_skin="anvil_woodfish",
            hide_anvil=False,
        )
        pw, ph = int(_GAME_W * _SCALE), int(_GAME_H * _SCALE)
        pix = QPixmap(pw, ph)
        pix.fill(Qt.transparent)
        p2 = _P(pix)
        p2.setRenderHint(_P.Antialiasing)
        p2.scale(_SCALE, _SCALE)
        _woodfish_game(p2, state)
        p2.end()
        cx, cy, cw, ch = _WOODFISH_CROP
        cropped = pix.copy(QRect(cx, cy, cw, ch))
        scaled  = cropped.scaled(w, h, Qt.KeepAspectRatio,
                                 Qt.SmoothTransformation)
        painter.drawPixmap((w - scaled.width())  // 2,
                           (h - scaled.height()) // 2, scaled)
    return draw


def _mallet_thumb_fn() -> Callable:
    def draw(painter, w, h):
        import math
        from types import SimpleNamespace
        from PyQt5.QtGui import QPixmap, QPainter as _P
        from PyQt5.QtCore import Qt, QRect
        from config import KB_X, IDLE_ANGLE
        state = SimpleNamespace(
            vcx=float(KB_X), vcy=float(_HAMMER_VCY),
            hammer_angle=lambda: IDLE_ANGLE,
            kb_mode="normal", typing_charge=0, typing_max_charge=5,
            charge_pulses=[], active_hammer_skin="hammer_mallet",
            play_time=0.0, hide_anvil=False,
            current_metal=None, current_chest=None, show_metal_forge=True,
        )
        pw, ph = int(_GAME_W * _SCALE), int(_GAME_H * _SCALE)
        pix = QPixmap(pw, ph)
        pix.fill(Qt.transparent)
        p2 = _P(pix)
        p2.setRenderHint(_P.Antialiasing)
        p2.scale(_SCALE, _SCALE)
        _mallet_game(p2, state)
        p2.end()
        cx, cy, cw, ch = _HAMMER_CROP
        cropped = pix.copy(QRect(cx, cy, cw, ch))
        scaled  = cropped.scaled(w, h, Qt.KeepAspectRatio,
                                 Qt.SmoothTransformation)
        painter.drawPixmap((w - scaled.width())  // 2,
                           (h - scaled.height()) // 2, scaled)
    return draw


# ── Register ──────────────────────────────────────────────────────────────────
_register("anvil_woodfish", "木魚",   "anvil",  None,
          draw_thumb=_woodfish_thumb_fn(), draw_game=_woodfish_game,
          draw_spark=_wf_draw_spark, draw_ember=_wf_draw_ember,
          draw_material=_wf_draw_material)
_register("hammer_mallet",  "木魚棍", "hammer", None,
          draw_thumb=_mallet_thumb_fn(),  draw_game=_mallet_game)
