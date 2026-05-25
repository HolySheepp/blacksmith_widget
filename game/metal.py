"""
Metal forging system.

Five metal types appear on the anvil after the first strike.
Each has a hidden quality bar; player's strike force fills it.
When full, the next strike triggers a flash → disappear → respawn cycle.

Quality unit = same as force_count increment
  (combo: 1 per hit; charge: charge_n per hit)
"""
import random

# ── Metal type definitions ─────────────────────────────────────────────────────

METAL_TYPES = [
    # hot_color = 剛生成時（高溫），cold_color = 鍛造完成時（冷卻）
    # 熱色各異確保玩家在剛生成時即能辨識金屬種類
    {"name": "破銅", "number": 1, "quality_max":  10, "weight": 40,
     "hot_color": (215, 105, 40), "cold_color": (172, 96,  48)},   # 銅棕
    {"name": "爛鐵", "number": 2, "quality_max":  20, "weight": 30,
     "hot_color": (192,  48, 10), "cold_color": ( 85, 82,  88)},   # 暗灰
    {"name": "鐵",   "number": 3, "quality_max":  40, "weight": 20,
     "hot_color": (235,  72, 15), "cold_color": (118, 120, 130)},  # 中灰
    {"name": "鋼",   "number": 4, "quality_max":  70, "weight":  7,
     "hot_color": (205,  88, 25), "cold_color": ( 92, 110, 148)},  # 鋼藍灰
    {"name": "精金", "number": 5, "quality_max": 100, "weight":  3,
     "hot_color": (255, 215, 55), "cold_color": (228, 196,  90)},  # 金黃
]

# ── Visual constants ───────────────────────────────────────────────────────────

# Thickness (px, game space) at each of the 5 forging stages
STAGE_THICKNESS = [30, 26, 22, 18, 14]
COMPLETE_THICKNESS = 10          # thickness when quality bar is full

# ── Animation durations (seconds) ─────────────────────────────────────────────

SPAWN_DUR = 0.35    # scale-in animation
FLASH_DUR = 0.35    # completion flash/fade-out animation


# ── Helpers ───────────────────────────────────────────────────────────────────

def pick_metal() -> int:
    """Return a random metal type index, weighted by rarity."""
    return random.choices(
        range(len(METAL_TYPES)),
        weights=[m["weight"] for m in METAL_TYPES],
    )[0]


# ── MetalPiece ────────────────────────────────────────────────────────────────

class MetalPiece:
    """A single metal piece sitting on the anvil."""
    __slots__ = (
        "type_idx", "name", "number", "quality_max",
        "quality", "spawn_t", "complete", "flash_t", "dead",
    )

    def __init__(self, type_idx: int):
        meta             = METAL_TYPES[type_idx]
        self.type_idx    = type_idx
        self.name        = meta["name"]
        self.number      = meta["number"]
        self.quality_max = float(meta["quality_max"])
        self.quality     = 0.0
        self.spawn_t     = 0.0    # 0 → 1 : scale-in animation progress
        self.complete    = False  # quality filled; waiting for next strike
        self.flash_t     = 0.0   # 0 → 1 : completion flash/fade animation
        self.dead        = False  # remove after flash finishes

    # ── Derived properties ────────────────────────────────────────────────

    @property
    def ratio(self) -> float:
        """Forging progress 0.0 → 1.0."""
        return min(1.0, self.quality / self.quality_max)

    @property
    def stage(self) -> int:
        """Visual thickness stage 0–4."""
        return min(4, int(self.ratio * 5))

    @property
    def thickness(self) -> float:
        return float(COMPLETE_THICKNESS if self.complete
                     else STAGE_THICKNESS[self.stage])

    @property
    def color(self) -> tuple:
        meta       = METAL_TYPES[self.type_idx]
        r1, g1, b1 = meta["hot_color"]
        r2, g2, b2 = meta["cold_color"]
        t = self.ratio
        return (int(r1 + t * (r2 - r1)),
                int(g1 + t * (g2 - g1)),
                int(b1 + t * (b2 - b1)))

    # ── Mutation ──────────────────────────────────────────────────────────

    def add_quality(self, force: float) -> bool:
        """Add force to quality bar.  Returns True if quality just filled."""
        if self.complete or self.dead:
            return False
        prev          = self.quality
        self.quality  = min(self.quality_max, self.quality + force)
        if self.quality >= self.quality_max and prev < self.quality_max:
            self.complete = True
            return True
        return False
