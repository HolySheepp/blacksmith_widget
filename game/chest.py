"""
Chest system — treasure boxes that occasionally drop on the anvil.

Three tiers:
  0 = Wood  (木寶箱, 淺棕色, quality 40)
  1 = Iron  (鐵寶箱, 銀灰色, quality 70)
  2 = Gold  (金寶箱, 金黃色, quality 100)

Drop trigger: every CHEST_HIT_MIN–CHEST_HIT_MAX hits, one chest is guaranteed.
Type is decided by weighted random (chest_wood_weight : chest_iron_weight : chest_gold_weight).

Smashing: player fills quality bar → crack_level grows (0-5) → shake intensifies →
          final strike opens chest → reward drops (skin or material).
"""
import random

from config import CHEST_W, CHEST_H, CHEST_LID_H, CHEST_BODY_H

# ── Chest type definitions ────────────────────────────────────────────────────

CHEST_TYPES = [
    {   # 0 — Wood
        "name":       "木寶箱",
        "tier":       0,
        "quality_max": 40,
        "body_color":  (139,  94,  60),   # warm brown
        "lid_color":   (165, 118,  75),   # lighter brown lid
        "strap_color": (100,  65,  30),   # dark leather straps
        "lock_color":  (185, 148,  42),   # brass lock
        "glow_color":  (210, 165,  85),   # warm amber glow
        "mat_type":    "破銅",            # fallback drop (MATERIAL_IDS[0])
        "mat_range":   (8, 18),
    },
    {   # 1 — Iron
        "name":       "鐵寶箱",
        "tier":       1,
        "quality_max": 70,
        "body_color":  (138, 148, 158),   # steel gray
        "lid_color":   (165, 175, 185),   # lighter gray lid
        "strap_color": ( 88,  96, 105),   # dark iron straps
        "lock_color":  (215, 220, 225),   # silver lock
        "glow_color":  (175, 210, 245),   # cool blue-silver glow
        "mat_type":    "鐵",              # fallback drop (MATERIAL_IDS[2])
        "mat_range":   (5, 12),
    },
    {   # 2 — Gold
        "name":       "金寶箱",
        "tier":       2,
        "quality_max": 100,
        "body_color":  (192, 155,  26),   # deep gold
        "lid_color":   (222, 183,  44),   # bright gold lid
        "strap_color": (145, 112,  10),   # dark gold straps
        "lock_color":  (255, 232,  80),   # bright gold lock
        "glow_color":  (255, 215,  50),   # brilliant gold glow
        "mat_type":    "精金",            # fallback drop (MATERIAL_IDS[4])
        "mat_range":   (3, 6),
    },
]

# ── Crack patterns ────────────────────────────────────────────────────────────
# Each level adds more cracks.  Coordinates are (fx, fy) fractions of the
# chest body (body_x + fx*CHEST_W, body_top + fy*CHEST_BODY_H).
# Level 0 = no cracks; levels 1–5 defined here (index = crack_level - 1).
CRACK_PATTERNS = [
    # Level 1: one hairline crack
    [((0.22, 0.05), (0.38, 0.62))],
    # Level 2: two cracks
    [((0.22, 0.05), (0.38, 0.62)),
     ((0.64, 0.08), (0.52, 0.68))],
    # Level 3: branching crack
    [((0.22, 0.05), (0.38, 0.62)),
     ((0.64, 0.08), (0.52, 0.68)),
     ((0.38, 0.62), (0.46, 0.95))],
    # Level 4: diagonal cross-crack appears
    [((0.22, 0.05), (0.38, 0.62)),
     ((0.64, 0.08), (0.52, 0.68)),
     ((0.38, 0.62), (0.46, 0.95)),
     ((0.48, 0.15), (0.28, 0.80))],
    # Level 5: heavily cracked
    [((0.22, 0.05), (0.38, 0.62)),
     ((0.64, 0.08), (0.52, 0.68)),
     ((0.38, 0.62), (0.46, 0.95)),
     ((0.48, 0.15), (0.28, 0.80)),
     ((0.08, 0.38), (0.62, 0.28))],
]

# ── Default drop settings ─────────────────────────────────────────────────────
CHEST_WEIGHTS_DEFAULT = [60, 30, 10]  # wood : iron : gold (weights, not %)
CHEST_HIT_MIN         = 8_000
CHEST_HIT_MAX         = 12_000

# ── Animation durations ───────────────────────────────────────────────────────
CHEST_SPAWN_DUR = 0.35   # scale-in
CHEST_OPEN_DUR  = 0.55   # opening burst + fade

# ── Skin display names (for UI) ───────────────────────────────────────────────
def _build_skin_display_names():
    from game.skin_registry import SKIN_REGISTRY
    return {sk: sd.label for sk, sd in SKIN_REGISTRY.items()
            if not sk.endswith("_default")}

SKIN_DISPLAY_NAMES = _build_skin_display_names()


# ── ChestPiece ────────────────────────────────────────────────────────────────

class ChestPiece:
    """A single treasure chest sitting on the anvil."""
    __slots__ = (
        "chest_type", "name", "quality_max",
        "quality", "spawn_t", "complete", "flash_t", "dead",
        "crack_level", "shake_t",
    )

    def __init__(self, chest_type: int):
        meta             = CHEST_TYPES[chest_type]
        self.chest_type  = chest_type
        self.name        = meta["name"]
        self.quality_max = float(meta["quality_max"])
        self.quality     = 0.0
        self.spawn_t     = 0.0       # 0→1 scale-in animation
        self.complete    = False
        self.flash_t     = 0.0       # 0→1 opening animation
        self.dead        = False
        self.crack_level = 0         # 0–5
        self.shake_t     = 0.0       # seconds remaining in current shake burst

    @property
    def ratio(self) -> float:
        return min(1.0, self.quality / self.quality_max)

    def add_quality(self, force: float) -> bool:
        """Add force to quality bar.  Returns True if quality just filled."""
        if self.complete or self.dead:
            return False
        prev          = self.quality
        self.quality  = min(self.quality_max, self.quality + force)
        # Crack level: 5 stages split evenly by ratio (0→0.2→...→1.0)
        new_crack = min(5, int(self.ratio * 5 + 0.001))
        if new_crack > self.crack_level:
            self.crack_level = new_crack
        # Shake: more violent as more damaged
        self.shake_t = 0.22 + self.ratio * 0.30
        if self.quality >= self.quality_max and prev < self.quality_max:
            self.complete = True
            return True
        return False


# ── Helpers ───────────────────────────────────────────────────────────────────

def pick_chest(weights) -> int:
    """Return a random chest tier index using given weights list."""
    return random.choices(range(len(CHEST_TYPES)), weights=weights)[0]


def pick_reward(chest_type: int, owned_skins: set) -> dict:
    """
    Decide what falls out of an opened chest.
    Returns {"skin": skin_id} if a new skin is available,
    else {"material": (mat_type, amount)}.
    """
    from game.skin_registry import SKIN_REGISTRY
    available = [sk for sk, sd in SKIN_REGISTRY.items()
                 if sd.chest_tier == chest_type and sk not in owned_skins]
    if available:
        return {"skin": random.choice(available)}
    mat_type  = CHEST_TYPES[chest_type]["mat_type"]
    lo, hi    = CHEST_TYPES[chest_type]["mat_range"]
    return {"material": (mat_type, random.randint(lo, hi))}
