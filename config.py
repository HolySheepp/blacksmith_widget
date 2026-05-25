"""
All game constants live here.
Game logic runs in the original 800×600 coordinate space.
SCALE is applied only at render time.
"""
import math

VERSION = "v0.3.1"   # bump this before every release

# ── Display ───────────────────────────────────────────────────────────────────
WIDGET_W = 480
WIDGET_H = 360
SCALE    = WIDGET_W / 800   # 0.6  (800×600 → 480×360)

# ── Game coordinate space ─────────────────────────────────────────────────────
GAME_W = 800
GAME_H = 600

# ── Anvil geometry ────────────────────────────────────────────────────────────
AX       = 390
AY_BASE  = 490
FACE_TOP = AY_BASE - 160   # 330  — striking surface
FACE_L   = AX - 110        # 280
FACE_R   = AX + 115        # 505

# ── Hammer geometry ───────────────────────────────────────────────────────────
GRIP_TO_BUTT = 35
HEAD_OFFSET  = 62           # grip → head centre (along handle)
HEAD_THICK   = 9            # head half-thickness along handle
HEAD_PERP    = 30           # head half-width perpendicular to handle

HL = HEAD_OFFSET - HEAD_THICK   # 53
HR = HEAD_OFFSET + HEAD_THICK   # 71
HP = HEAD_PERP                  # 30

# ── Rotation ──────────────────────────────────────────────────────────────────
IDLE_ANGLE    = -(math.pi / 2 - 0.3)   # ≈ -1.271, head upper-right of grip
SWING_ANGLE   = -math.pi                # head left, face pointing down
APPROACH_DIST = 220

# ── Keyboard target ───────────────────────────────────────────────────────────
KB_X    = AX + HEAD_OFFSET  # 452
KB_Y    = 232               # ready hover height
MAX_VCY = FACE_TOP - HEAD_PERP  # 300 — hard floor

# ── Spring physics ────────────────────────────────────────────────────────────
KX = 90     # horizontal spring constant
DX = 12     # horizontal damping
KY = 120    # vertical spring constant
DY = round(7 * math.sqrt(KY / 60) * 10) / 10   # ≈ 9.9

# ── Typing mode defaults ──────────────────────────────────────────────────────
TYPING_BASE_MS    = 520
TYPING_MAX_CHARGE = 5

# ── Charge colours: forge temperature gradient (dark embers → blue-white) ─────
# 梯度說明：暗紅 → 橙 → 琥珀 → 近白熱 → 淡藍白 → 藍白（越藍越高溫，符合玩家直覺）
# 8 段改為淡黃白（避免與暴擊金色 (255,230,50) 混淆）
# 9 段改為淡藍白（過渡到藍白）
CHARGE_COLORS = [
    (0x99, 0x11, 0x00),   #  1  dark ember red
    (0xcc, 0x22, 0x00),   #  2  dark cherry
    (0xff, 0x22, 0x00),   #  3  cherry red
    (0xff, 0x44, 0x00),   #  4  red-orange
    (0xff, 0x66, 0x00),   #  5  bright orange-red
    (0xff, 0x8c, 0x00),   #  6  orange
    (0xff, 0xaa, 0x00),   #  7  amber
    (0xff, 0xf0, 0xc0),   #  8  pale warm white (near white-hot — clearly ≠ crit gold)
    (0xdc, 0xee, 0xff),   #  9  pale blue-white (bridging to full blue-white)
    (0xaa, 0xdd, 0xff),   # 10  blue-white (over-heated)
]

def get_charge_color(cf: float) -> tuple[int, int, int]:
    """Return (r, g, b) for charge fraction 0.0–1.0."""
    idx = min(9, int(cf * 10))
    return CHARGE_COLORS[idx]


# ── Charge-EX mode ───────────────────────────────────────────────────────────
CHARGE_EX_LIFT     = 500.0   # default upward velocity kick per click (game units / s)
CHARGE_EX_IDLE_MS  = 200.0   # ms of inactivity before auto-slam triggers

# ── Turbo / Fever mode defaults ───────────────────────────────────────────────
FEVER_THRESHOLD = 2       # consecutive full-charge hits to trigger fever
FEVER_DURATION  = 20.0    # fever active duration (seconds)
FEVER_COOLDOWN  = 75.0    # post-fever cooldown duration (seconds)
