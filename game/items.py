"""
Item catalog — workstation craftables, shop upgrades.

Cost tuples follow the same order as METAL_TYPES:
  (破銅, 爛鐵, 鐵, 鋼, 精金)
"""

# ── Craftable items ────────────────────────────────────────────────────────────

ITEMS = [
    # blueprint_required=False → available from the start
    {
        "id":                "iron_nail",
        "name":              "鐵釘",
        "blueprint_required": False,
        "cost":              (5, 3, 0, 0, 0),
        "craft_clicks":      40,
        "sell_price":        8,
    },
    {
        "id":                "horseshoe",
        "name":              "馬蹄鐵",
        "blueprint_required": False,
        "cost":              (0, 0, 5, 0, 0),
        "craft_clicks":      70,
        "sell_price":        22,
    },
    {
        "id":                "armor_plate",
        "name":              "鎧甲板",
        "blueprint_required": True,
        "cost":              (0, 8, 5, 0, 0),
        "craft_clicks":      110,
        "sell_price":        45,
    },
    {
        "id":                "short_sword",
        "name":              "短刀",
        "blueprint_required": True,
        "cost":              (0, 0, 4, 2, 0),
        "craft_clicks":      120,
        "sell_price":        65,
    },
    {
        "id":                "iron_shield",
        "name":              "鑄鐵盾",
        "blueprint_required": True,
        "cost":              (0, 0, 10, 3, 0),
        "craft_clicks":      160,
        "sell_price":        95,
    },
    {
        "id":                "longsword",
        "name":              "長劍",
        "blueprint_required": True,
        "cost":              (0, 0, 0, 6, 1),
        "craft_clicks":      200,
        "sell_price":        140,
    },
    {
        "id":                "mithril_ornament",
        "name":              "精金飾品",
        "blueprint_required": True,
        "cost":              (0, 0, 0, 0, 4),
        "craft_clicks":      170,
        "sell_price":        260,
    },
]

ITEMS_BY_ID = {it["id"]: it for it in ITEMS}


# ── Shop catalog ───────────────────────────────────────────────────────────────

# Hammers — affect base force multiplier and crit rate.
# force_mult scales _metal_force on every hit; also benefits crafting speed.
HAMMERS = [
    {"id": "iron_hammer",    "name": "木柄鐵錘", "price":    0,
     "force_mult": 1.0, "crit_rate": 0.05},
    {"id": "wrought_hammer", "name": "熟鐵槌",   "price":  300,
     "force_mult": 1.5, "crit_rate": 0.07},
    {"id": "steel_hammer",   "name": "鋼頭大錘", "price":  900,
     "force_mult": 2.2, "crit_rate": 0.10},
    {"id": "mithril_hammer", "name": "精金神錘", "price": 3500,
     "force_mult": 3.8, "crit_rate": 0.15},
]

# Anvils — a CHOICE of playstyle, not a linear upgrade.
#   mode=None  → allows all modes (settings selector still works)
#   mode="combo"  → locked to combo mode; higher raw throughput, base force = 1
#   mode="turbo"  → locked to turbo/fever mode; combines approaches, requires upkeep
#
# Bonus keys (applied in state.py):
#   "fever_duration_mult" → multiplier on state.fever_duration
#   None                  → no bonus
ANVILS = [
    {"id": "old_anvil",   "name": "舊鐵砧",    "price":    0,
     "mode": None,    "bonus": None},
    {"id": "combo_anvil", "name": "鋼製連打砧", "price":  500,
     "mode": "combo", "bonus": None},
    {"id": "turbo_anvil", "name": "精金渦輪砧", "price": 1500,
     "mode": "turbo", "bonus": "fever_duration_mult:1.5"},
]

# Supply contracts — change metal spawn weights (破銅/爛鐵/鐵/鋼/精金).
# Higher-tier contracts shift probability toward rarer metals.
CONTRACTS = [
    {"id": "standard",         "name": "標準合約",  "price":    0,
     "weights": (40, 30, 20,  7,  3)},
    {"id": "iron_contract",    "name": "精鐵合約",  "price":  400,
     "weights": (20, 20, 30, 20, 10)},
    {"id": "refined_contract", "name": "精煉合約",  "price": 1600,
     "weights": ( 5, 10, 20, 35, 30)},
]

# Blueprints — sold in shop; unlock the corresponding item for crafting.
BLUEPRINTS = [
    {"id": "bp_armor_plate",       "name": "鎧甲板藍圖",   "unlocks": "armor_plate",      "price":  80},
    {"id": "bp_short_sword",       "name": "短刀藍圖",     "unlocks": "short_sword",      "price": 150},
    {"id": "bp_iron_shield",       "name": "鑄鐵盾藍圖",   "unlocks": "iron_shield",      "price": 220},
    {"id": "bp_longsword",         "name": "長劍藍圖",     "unlocks": "longsword",        "price": 380},
    {"id": "bp_mithril_ornament",  "name": "精金飾品藍圖", "unlocks": "mithril_ornament", "price": 700},
]

# Customer commission parameters
DELIVERY_CLICKS    = 40    # clicks required to complete any delivery
COMMISSION_MARKUP  = 1.15  # gold reward = sell_price × qty × markup
COMMISSION_MAX_QTY = 3     # maximum items per commission order
