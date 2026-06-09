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

# ── Custom-shape skin template ────────────────────────────────────────────────
# Uncomment and fill in to add a completely new shape.
#
# def _woodfish_thumb(painter, w, h):
#     from PyQt5.QtGui import QColor, QBrush
#     from PyQt5.QtCore import Qt
#     # draw a small woodfish in w×h space
#
# def _woodfish_game(painter, state):
#     from PyQt5.QtGui import QColor, QBrush
#     from PyQt5.QtCore import Qt
#     # draw full-size animated woodfish (replaces the anvil in-game)
#
# _register("anvil_woodfish", "木魚", "anvil", chest_tier=1,
#           draw_thumb=_woodfish_thumb, draw_game=_woodfish_game)
