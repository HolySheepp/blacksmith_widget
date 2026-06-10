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
    skin_id:    str
    label:      str
    slot:       str                      # "anvil" or "hammer"
    chest_tier: Optional[int]            # 0 / 1 / 2 / None
    draw_thumb: Callable                 # (painter, w, h) → None
    draw_game:  Optional[Callable]       # (painter, state) → None  or  None


SKIN_REGISTRY: dict[str, SkinDef] = {}  # insertion-ordered (Python 3.7+)


def _register(skin_id: str, label: str, slot: str,
              chest_tier: Optional[int],
              draw_thumb: Callable,
              draw_game:  Optional[Callable] = None):
    SKIN_REGISTRY[skin_id] = SkinDef(
        skin_id, label, slot, chest_tier, draw_thumb, draw_game)


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
# ══════════════════════════════════════════════════════════════════════════════

# ── In-game draw functions ────────────────────────────────────────────────────

def _woodfish_game(painter, state):
    """Draw a wooden fish (木魚) replacing the anvil.
    Top of fish body = FACE_TOP = 330, the hammer's strike point."""
    from PyQt5.QtGui import QColor, QBrush, QPen
    from PyQt5.QtCore import Qt, QRectF
    from config import AX, FACE_TOP

    cx    = float(AX)        # 390
    top_y = float(FACE_TOP)  # 330 — strike point = top of fish
    bw, bh = 200.0, 128.0

    painter.setPen(Qt.NoPen)

    # 1. Ground shadow
    painter.setBrush(QBrush(QColor(0, 0, 0, 55)))
    painter.drawEllipse(QRectF(cx - bw/2 + 10, top_y + bh - 8, bw - 14, 20))

    # 2. Dark underside (full ellipse — bottom will peek out)
    painter.setBrush(QBrush(QColor(60, 35, 10)))
    painter.drawEllipse(QRectF(cx - bw/2, top_y, bw, bh))

    # 3. Main body colour (top 78% of height)
    painter.setBrush(QBrush(QColor(108, 68, 26)))
    painter.drawEllipse(QRectF(cx - bw/2, top_y, bw, bh * 0.78))

    # 4. Mid highlight band
    painter.setBrush(QBrush(QColor(130, 83, 32)))
    painter.drawEllipse(QRectF(cx - bw*0.45, top_y + bh*0.12, bw*0.9, bh*0.52))

    # 5. Top dome specular highlight
    painter.setBrush(QBrush(QColor(165, 108, 46, 145)))
    painter.drawEllipse(QRectF(cx - bw*0.28, top_y + 5, bw*0.40, bh*0.27))

    # 6. Wood grain arcs
    painter.setPen(QPen(QColor(85, 52, 18, 115), 1.5))
    painter.setBrush(Qt.NoBrush)
    for frac in [0.38, 0.55, 0.70]:
        gy   = top_y + bh * frac
        half = bw / 2 * (1.0 - (frac - 0.5) ** 2 * 2.8) * 0.88
        if half > 8:
            painter.drawArc(QRectF(cx - half, gy - 5, half * 2, 10),
                            0, 180 * 16)
    painter.setPen(Qt.NoPen)

    # 7. Slit rim (raised edge around slot)
    slit_w, slit_h = 84.0, 18.0
    slit_cy = top_y + bh * 0.22
    painter.setBrush(QBrush(QColor(80, 50, 18)))
    painter.drawEllipse(QRectF(cx - slit_w/2 - 3, slit_cy - slit_h/2 - 3,
                               slit_w + 6, slit_h + 6))

    # 8. Slit opening
    painter.setBrush(QBrush(QColor(25, 12, 4)))
    painter.drawEllipse(QRectF(cx - slit_w/2, slit_cy - slit_h/2, slit_w, slit_h))

    # 9. Outer edge highlight line
    painter.setPen(QPen(QColor(155, 100, 42, 180), 1.8))
    painter.setBrush(Qt.NoBrush)
    painter.drawEllipse(QRectF(cx - bw/2, top_y, bw, bh))
    painter.setPen(Qt.NoPen)

    # 10. Heat glow (mirrors anvil logic)
    glow = max(getattr(state, 'anvil_glow', 0.0),
               getattr(state, 'heat_level', 0) * 0.22)
    if glow > 0.01:
        sc = getattr(state, 'strike_color', (255, 120, 20))
        painter.setBrush(QBrush(QColor(sc[0], sc[1], sc[2], int(glow * 175))))
        painter.drawEllipse(QRectF(cx - bw/2, top_y, bw, bh * 0.45))


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

_WOODFISH_CROP = (170, 188, 144, 92)   # (x, y, w, h) in 480×360 widget coords


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
          draw_thumb=_woodfish_thumb_fn(), draw_game=_woodfish_game)
_register("hammer_mallet",  "木魚棍", "hammer", None,
          draw_thumb=_mallet_thumb_fn(),  draw_game=_mallet_game)
