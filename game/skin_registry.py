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
    """'業力怨靈' — a troubled karma spirit being knocked into the fish's slit.
    Fixed size; each strike pushes it deeper until fully absorbed into the wood."""
    import math
    from PyQt5.QtGui import QColor, QBrush, QPen
    from PyQt5.QtCore import Qt, QPointF, QRectF, QRect
    from config import AX, FACE_TOP

    m = getattr(state, 'current_metal', None)
    if m is None or m.dead:
        return
    spawn_scale = min(1.0, m.spawn_t)
    if spawn_scale <= 0.01:
        return

    painter.save()

    if m.flash_t > 0.0:
        brightness = (m.flash_t / 0.3) if m.flash_t < 0.3 else 0.0
        alpha      = 255 if m.flash_t < 0.3 else max(
            0, int((1.0 - (m.flash_t - 0.3) / 0.7) * 255))
    else:
        brightness, alpha = 0.0, 255

    # Fixed size — only the position changes, not the radius
    r_base = 30.0 * spawn_scale
    r_x    = r_base * 1.12
    r_y    = r_base

    # Sinks from hovering above the slit → mostly submerged below it
    hover_cy    = float(FACE_TOP) - r_y - 20.0   # ratio=0: floating 20px above slit
    submerge_cy = float(FACE_TOP) + r_y * 0.6    # ratio=1: mostly inside the fish
    cx = float(AX)
    cy = hover_cy + m.ratio * (submerge_cy - hover_cy)

    # Idle jitter — makes the spirit look alive
    t_now = getattr(state, 'play_time', 0.0)
    j = m.ratio * 2.8
    cx += math.sin(t_now * 11.3) * j
    cy += math.cos(t_now *  9.1) * j * 0.65

    # Clip so the portion that has sunk below the slit is hidden
    painter.setClipRect(QRect(0, 0, 800, int(FACE_TOP) + 2))

    # Colour: dark charcoal → deep indigo-purple as karma accumulates
    br = int(40 + m.ratio * 55)
    bg = int(30 + m.ratio * 25)
    bb = int(52 + m.ratio * 74)
    if brightness > 0:
        br = min(255, int(br + brightness * (255 - br)))
        bg = min(255, int(bg + brightness * (255 - bg)))
        bb = min(255, int(bb + brightness * (255 - bb)))

    painter.setPen(Qt.NoPen)

    # 1. Drop shadow
    painter.setBrush(QBrush(QColor(0, 0, 0, int(alpha * 0.32))))
    painter.drawEllipse(QPointF(cx + 3, cy + 5), r_x * 0.88, r_y * 0.55)

    # 2. Outer aura — heavier karma = more oppressive halo
    aura_a = int(alpha * (0.10 + m.ratio * 0.20))
    painter.setBrush(QBrush(QColor(br, bg, bb, aura_a)))
    painter.drawEllipse(QPointF(cx, cy), r_x * 1.30, r_y * 1.30)

    # 3. Main orb body
    painter.setBrush(QBrush(QColor(br, bg, bb, alpha)))
    painter.drawEllipse(QPointF(cx, cy), r_x, r_y)

    # 4. Upper-left specular highlight
    painter.setBrush(QBrush(QColor(
        min(255, br + 58), min(255, bg + 46), min(255, bb + 72),
        int(alpha * 0.50))))
    painter.drawEllipse(QPointF(cx - r_x * 0.26, cy - r_y * 0.30),
                        r_x * 0.40, r_y * 0.34)

    # 5. Face — visible once orb is large enough
    if r_base >= 18 and alpha > 50:
        face_col = QColor(min(255, br + 108), min(255, bg + 90),
                          min(255, bb + 108), int(alpha * 0.78))
        eye_r  = max(1.3, r_base * 0.068)
        spread = r_base * 0.30
        eye_y  = cy - r_y * 0.08

        # Dot eyes
        painter.setBrush(QBrush(face_col))
        painter.drawEllipse(QPointF(cx - spread, eye_y), eye_r, eye_r)
        painter.drawEllipse(QPointF(cx + spread, eye_y), eye_r, eye_r)

        # Worried eyebrows (slope inward and downward)
        brow_len = r_base * 0.20
        brow_y   = eye_y - eye_r * 2.6
        pen = QPen(face_col, max(1.1, r_base * 0.052))
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawLine(
            QPointF(cx - spread - brow_len * 0.5, brow_y - brow_len * 0.32),
            QPointF(cx - spread + brow_len * 0.5, brow_y + brow_len * 0.32))
        painter.drawLine(
            QPointF(cx + spread + brow_len * 0.5, brow_y - brow_len * 0.32),
            QPointF(cx + spread - brow_len * 0.5, brow_y + brow_len * 0.32))

        # Frown (startAngle=0, spanAngle=-180*16 → clockwise through bottom)
        fw = r_base * 0.46
        fy = cy + r_y * 0.28
        painter.drawArc(
            QRectF(cx - fw / 2, fy - fw * 0.28, fw, fw * 0.56),
            0, -180 * 16)
        painter.setPen(Qt.NoPen)

    painter.restore()

    # ── Curved wood seam at junction ────────────────────────────────────────
    # Fades in as orb approaches FACE_TOP, creating a rounded-opening look
    seam_prox = (cy + r_y - (float(FACE_TOP) - 30.0)) / 30.0
    seam_a    = max(0.0, min(1.0, seam_prox))
    if seam_a > 0.01:
        from PyQt5.QtGui import QColor, QBrush
        from PyQt5.QtCore import Qt, QPointF
        rim_col = QColor(110, 75, 32, int(seam_a * min(alpha, 255)))
        painter.setBrush(QBrush(rim_col))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QPointF(cx, float(FACE_TOP) + 9.0), r_x * 0.93, 12.0)


def _make_mallet_game(ball_dark, ball_main, ball_hl, ball_edge, glow_rgb=(255, 102, 0)):
    """Factory: returns a draw_game function for a mallet with the given ball colours.
    The wooden handle is always the same; only the ball material changes."""
    def _draw(painter, state):
        import math
        from PyQt5.QtGui import QColor, QBrush, QPen
        from PyQt5.QtCore import Qt, QPointF
        from config import (HEAD_OFFSET, HEAD_PERP, GRIP_TO_BUTT,
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

        ball_r    = float(HEAD_PERP)
        stick_end = HEAD_OFFSET - ball_r + 5

        painter.setPen(Qt.NoPen)

        # ── Charge pulse rings ────────────────────────────────────────────────
        for pulse in state.charge_pulses:
            t  = pulse["t"]
            m2 = t * 20
            al = (1 - t) ** 1.4 * 0.9
            if al < 0.015:
                continue
            col = get_charge_color(pulse["cf"])
            bx, by = p(HEAD_OFFSET, 0)
            r_ring = ball_r + m2
            pen = QPen(QColor(col[0], col[1], col[2], int(al * 255)))
            pen.setWidthF(max(0.5, 2.2 - t * 1.4))
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(QPointF(bx, by), r_ring, r_ring)
            painter.setPen(Qt.NoPen)

        # ── Handle (stick) ────────────────────────────────────────────────────
        painter.setBrush(QBrush(QColor(100, 60, 20)))
        painter.drawPolygon(_poly([
            p(-GRIP_TO_BUTT, -6), p(-GRIP_TO_BUTT, 6),
            p(stick_end,      6), p(stick_end,     -6),
        ]))
        painter.setBrush(QBrush(QColor(148, 95, 38)))
        painter.drawPolygon(_poly([
            p(-GRIP_TO_BUTT, -6), p(-GRIP_TO_BUTT, -2),
            p(stick_end,     -2), p(stick_end,     -6),
        ]))
        painter.setBrush(QBrush(QColor(165, 108, 50)))
        painter.drawPolygon(_poly([
            p(-GRIP_TO_BUTT + 4, -4), p(-GRIP_TO_BUTT + 4, -1),
            p(stick_end - 2,     -1), p(stick_end - 2,     -4),
        ]))
        painter.setBrush(QBrush(QColor(58, 28, 10, 140)))
        for al in range(5, 36, 12):
            painter.drawPolygon(_poly([
                p(al, -6), p(al + 5, -6), p(al + 5, 6), p(al, 6),
            ]))
        painter.setBrush(QBrush(QColor(74, 74, 74)))
        painter.drawPolygon(_poly([
            p(-GRIP_TO_BUTT - 3, -7), p(-GRIP_TO_BUTT + 2, -7),
            p(-GRIP_TO_BUTT + 2,  7), p(-GRIP_TO_BUTT - 3,  7),
        ]))

        # ── Ball ──────────────────────────────────────────────────────────────
        bx, by = p(HEAD_OFFSET, 0)
        dr, dg, db     = ball_dark
        mr, mg, mb     = ball_main
        hr, hg, hb, ha = ball_hl
        er, eg, eb, ea = ball_edge
        gr, gg, gb     = glow_rgb

        painter.setBrush(QBrush(QColor(0, 0, 0, 55)))
        painter.drawEllipse(QPointF(bx + 3, by + 3), ball_r, ball_r)
        painter.setBrush(QBrush(QColor(dr, dg, db)))
        painter.drawEllipse(QPointF(bx, by), ball_r, ball_r)
        painter.setBrush(QBrush(QColor(mr, mg, mb)))
        painter.drawEllipse(QPointF(bx - 2, by - 2), ball_r * 0.90, ball_r * 0.90)
        painter.setBrush(QBrush(QColor(hr, hg, hb, ha)))
        painter.drawEllipse(QPointF(bx - ball_r * 0.28, by - ball_r * 0.28),
                            ball_r * 0.38, ball_r * 0.34)
        painter.setPen(QPen(QColor(er, eg, eb, ea), 1.5))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QPointF(bx, by), ball_r, ball_r)
        painter.setPen(Qt.NoPen)

        # ── Proximity glow ────────────────────────────────────────────────────
        prox = max(0.0, 1.0 - max(0.0, FACE_TOP - state.vcy) / 120.0)
        if prox > 0.01:
            painter.setBrush(QBrush(QColor(gr, gg, gb, int(prox * 0.55 * 255))))
            painter.drawEllipse(QPointF(bx, by), ball_r, ball_r)
            if prox > 0.5:
                painter.setBrush(QBrush(
                    QColor(255, 255, 255, int((prox - 0.5) * 0.28 * 255))))
                painter.drawEllipse(QPointF(bx, by), ball_r * 0.6, ball_r * 0.6)

        # ── Charge fill ───────────────────────────────────────────────────────
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

    return _draw


_mallet_game        = _make_mallet_game(
    ball_dark=(75, 44, 14), ball_main=(118, 74, 26),
    ball_hl=(175, 118, 50, 160), ball_edge=(155, 100, 42, 180),
    glow_rgb=(255, 102, 0))

_silver_mallet_game = _make_mallet_game(
    ball_dark=(68, 78, 92), ball_main=(135, 150, 168),
    ball_hl=(200, 218, 235, 160), ball_edge=(158, 175, 195, 180),
    glow_rgb=(170, 195, 220))

_gold_mallet_game   = _make_mallet_game(
    ball_dark=(115, 85, 15), ball_main=(192, 155, 32),
    ball_hl=(248, 220, 85, 160), ball_edge=(210, 175, 48, 180),
    glow_rgb=(255, 198, 40))


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


def _mallet_thumb_fn(game_fn=None, skin_id="hammer_mallet") -> Callable:
    if game_fn is None:
        game_fn = _mallet_game

    def draw(painter, w, h):
        from types import SimpleNamespace
        from PyQt5.QtGui import QPixmap, QPainter as _P
        from PyQt5.QtCore import Qt, QRect
        from config import KB_X, IDLE_ANGLE
        state = SimpleNamespace(
            vcx=float(KB_X), vcy=float(_HAMMER_VCY),
            hammer_angle=lambda: IDLE_ANGLE,
            kb_mode="normal", typing_charge=0, typing_max_charge=5,
            charge_pulses=[], active_hammer_skin=skin_id,
            play_time=0.0, hide_anvil=False,
            current_metal=None, current_chest=None, show_metal_forge=True,
        )
        pw, ph = int(_GAME_W * _SCALE), int(_GAME_H * _SCALE)
        pix = QPixmap(pw, ph)
        pix.fill(Qt.transparent)
        p2 = _P(pix)
        p2.setRenderHint(_P.Antialiasing)
        p2.scale(_SCALE, _SCALE)
        game_fn(p2, state)
        p2.end()
        cx, cy, cw, ch = _HAMMER_CROP
        cropped = pix.copy(QRect(cx, cy, cw, ch))
        scaled  = cropped.scaled(w, h, Qt.KeepAspectRatio,
                                 Qt.SmoothTransformation)
        painter.drawPixmap((w - scaled.width())  // 2,
                           (h - scaled.height()) // 2, scaled)
    return draw


# ── 年糕臼 / 打糕錘 skins ──────────────────────────────────────────────────────

def _mochi_anvil_game(painter, state):
    """Draw a stone mortar (年糕臼) replacing the anvil.
    Bowl opening at FACE_TOP; thick walls taper to a flat base."""
    from PyQt5.QtGui import QColor, QBrush, QPen, QPolygonF
    from PyQt5.QtCore import Qt, QPointF, QRectF
    from config import AX, FACE_TOP

    cx  = float(AX)
    top = float(FACE_TOP)
    bot = top + 150.0
    ow  = 90.0   # outer half-width at top
    iw  = 60.0   # inner bowl half-width at top

    def Q(pts):
        return QPolygonF([QPointF(x, y) for x, y in pts])

    painter.setPen(Qt.NoPen)

    # Ground shadow
    painter.setBrush(QBrush(QColor(0, 0, 0, 55)))
    painter.drawEllipse(QRectF(cx - 72, bot - 5, 144, 18))

    # Outer stone body (left wall, right wall, base slab)
    stone_main = QColor(128, 120, 112)
    painter.setBrush(QBrush(stone_main))
    painter.drawPolygon(Q([
        (cx - ow, top), (cx - iw, top + 4),
        (cx - iw + 6, bot - 12), (cx - ow + 4, bot),
    ]))
    painter.drawPolygon(Q([
        (cx + iw, top + 4), (cx + ow, top),
        (cx + ow - 4, bot), (cx + iw - 6, bot - 12),
    ]))
    painter.drawRoundedRect(QRectF(cx - ow + 4, bot - 14, (ow - 4) * 2, 20), 5, 5)

    # Stone highlights on wall faces
    painter.setBrush(QBrush(QColor(152, 144, 136)))
    painter.drawPolygon(Q([
        (cx - ow, top), (cx - ow + 10, top),
        (cx - iw + 12, bot - 12), (cx - ow + 4, bot - 12),
    ]))
    painter.drawPolygon(Q([
        (cx + ow - 10, top), (cx + ow, top),
        (cx + ow - 4, bot - 12), (cx + iw - 12, bot - 12),
    ]))

    # Inner bowl (dark shadow — the hollow)
    painter.setBrush(QBrush(QColor(70, 64, 58)))
    painter.drawPolygon(Q([
        (cx - iw, top + 4), (cx + iw, top + 4),
        (cx + iw - 6, bot - 12), (cx - iw + 6, bot - 12),
    ]))

    # Bowl bottom highlight
    painter.setBrush(QBrush(QColor(88, 82, 76)))
    painter.drawEllipse(QRectF(cx - 28, bot - 26, 56, 16))

    # Top rim (raised ledge at FACE_TOP)
    rim_w = ow + 5.0
    painter.setBrush(QBrush(QColor(162, 154, 146)))
    painter.drawRoundedRect(QRectF(cx - rim_w, top - 3, rim_w * 2, 14), 4, 4)
    painter.setBrush(QBrush(QColor(188, 180, 172)))
    painter.drawRoundedRect(QRectF(cx - rim_w + 2, top - 3, rim_w * 2 - 4, 5), 2, 2)

    # Rim notch where pestle hits
    painter.setBrush(QBrush(QColor(138, 130, 122)))
    painter.drawEllipse(QRectF(cx - 42, top + 2, 84, 8))


def _mochi_draw_material(painter, state):
    """Draw elastic mochi (年糕) being pounded in the mortar.
    Flattens with ratio; squishes elastically on each hit."""
    import math
    from PyQt5.QtGui import QColor, QBrush
    from PyQt5.QtCore import Qt, QPointF, QRectF
    from config import AX, FACE_TOP

    m = getattr(state, 'current_metal', None)
    if m is None or m.dead:
        return
    spawn_scale = min(1.0, m.spawn_t)
    if spawn_scale <= 0.01:
        return

    if m.flash_t > 0.0:
        flash_rise  = m.flash_t * 85.0
        flash_alpha = max(0, int((1.0 - m.flash_t) * 255))
        flash_str   = m.flash_t * 0.35   # stretches taller during launch
    else:
        flash_rise = flash_str = 0.0
        flash_alpha = 255

    # Elastic hit deformation via hit_cooldown (380ms window)
    hcool = getattr(state, 'hit_cooldown', 0.0)
    hit_t = 1.0 - min(1.0, hcool / 380.0)
    squish = math.sin(hit_t * math.pi * 2.6) * math.exp(-hit_t * 3.8)

    # Idle bounce
    t_now = getattr(state, 'play_time', 0.0)
    idle  = math.sin(t_now * 5.8) * 0.038

    # Shape: ry shrinks (mochi flattens), rx grows (spreads out)
    base_ry = (40.0 * (1.0 - m.ratio * 0.70) + flash_str * 18.0) * spawn_scale
    base_rx = (62.0 * (1.0 + m.ratio * 0.40)) * spawn_scale
    ry = max(4.0, base_ry * (1.0 - squish * 0.55 + idle))
    rx = base_rx * (1.0 + squish * 0.40 - idle * 0.4)

    cx = float(AX)
    cy = float(FACE_TOP) - ry - flash_rise

    painter.save()
    painter.setPen(Qt.NoPen)

    # Drop shadow under mochi
    painter.setBrush(QBrush(QColor(148, 138, 110, int(55 * flash_alpha / 255))))
    painter.drawEllipse(QRectF(cx - rx * 0.82 + 3, float(FACE_TOP) - 4,
                               rx * 1.64, 9))

    # Main mochi body (warm cream)
    painter.setBrush(QBrush(QColor(242, 238, 222, flash_alpha)))
    painter.drawEllipse(QPointF(cx, cy), rx, ry)

    # Lower shadow (inside — represents curvature)
    painter.setBrush(QBrush(QColor(208, 198, 172, int(flash_alpha * 0.52))))
    painter.drawEllipse(QPointF(cx, cy + ry * 0.3), rx * 0.85, ry * 0.58)

    # Top specular highlight
    painter.setBrush(QBrush(QColor(255, 254, 248, int(flash_alpha * 0.80))))
    painter.drawEllipse(QPointF(cx - rx * 0.22, cy - ry * 0.28), rx * 0.44, ry * 0.35)

    painter.restore()


def _mochi_draw_spark(painter, spark):
    """Soft flour-dust puffs rising from the mochi."""
    from PyQt5.QtGui import QColor, QBrush
    from PyQt5.QtCore import Qt, QPointF
    al = spark.frac
    sz = spark.size * al * 1.9
    if sz < 0.5:
        return
    painter.setBrush(QBrush(QColor(248, 246, 240, int(al * 0.60 * 255))))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(QPointF(spark.x, spark.y), sz, sz)
    painter.setBrush(QBrush(QColor(255, 255, 255, int(al * 0.32 * 255))))
    painter.drawEllipse(QPointF(spark.x, spark.y), sz * 0.48, sz * 0.48)


_mochi_mallet_game = _make_mallet_game(
    ball_dark=(95, 72, 32), ball_main=(162, 125, 58),
    ball_hl=(215, 185, 115, 160), ball_edge=(175, 138, 68, 180),
    glow_rgb=(255, 215, 125))


def _mochi_anvil_thumb_fn() -> Callable:
    _MOCHI_CROP = (175, 194, 118, 96)   # (x, y, w, h) in widget coords

    def draw(painter, w, h):
        from types import SimpleNamespace
        from PyQt5.QtGui import QPixmap, QPainter as _P
        from PyQt5.QtCore import Qt, QRect
        state = SimpleNamespace(
            vcx=0.0, vcy=0.0, hide_anvil=False,
            current_metal=None, current_chest=None, show_metal_forge=True,
            active_anvil_skin="anvil_mochi", anvil_glow=0.0, heat_level=0.0,
        )
        pw, ph = int(_GAME_W * _SCALE), int(_GAME_H * _SCALE)
        pix = QPixmap(pw, ph)
        pix.fill(Qt.transparent)
        p2 = _P(pix)
        p2.setRenderHint(_P.Antialiasing)
        p2.scale(_SCALE, _SCALE)
        _mochi_anvil_game(p2, state)
        p2.end()
        cx2, cy2, cw2, ch2 = _MOCHI_CROP
        cropped = pix.copy(QRect(cx2, cy2, cw2, ch2))
        scaled  = cropped.scaled(w, h, Qt.KeepAspectRatio,
                                 Qt.SmoothTransformation)
        painter.drawPixmap((w - scaled.width())  // 2,
                           (h - scaled.height()) // 2, scaled)
    return draw


# ── Register ──────────────────────────────────────────────────────────────────
_register("anvil_woodfish", "木魚",   "anvil",  0,
          draw_thumb=_woodfish_thumb_fn(), draw_game=_woodfish_game,
          draw_spark=_wf_draw_spark, draw_ember=_wf_draw_ember,
          draw_material=_wf_draw_material)
_register("hammer_mallet",       "木魚棍",   "hammer", 0,
          draw_thumb=_mallet_thumb_fn(),
          draw_game=_mallet_game)
_register("hammer_mallet_silver", "銀木魚棍", "hammer", 1,
          draw_thumb=_mallet_thumb_fn(_silver_mallet_game, "hammer_mallet_silver"),
          draw_game=_silver_mallet_game)
_register("hammer_mallet_gold",   "金木魚棍", "hammer", 2,
          draw_thumb=_mallet_thumb_fn(_gold_mallet_game, "hammer_mallet_gold"),
          draw_game=_gold_mallet_game)
_register("anvil_mochi", "年糕臼", "anvil", 1,
          draw_thumb=_mochi_anvil_thumb_fn(), draw_game=_mochi_anvil_game,
          draw_spark=_mochi_draw_spark, draw_material=_mochi_draw_material)
_register("hammer_mochi", "打糕錘", "hammer", 1,
          draw_thumb=_mallet_thumb_fn(_mochi_mallet_game, "hammer_mochi"),
          draw_game=_mochi_mallet_game)
