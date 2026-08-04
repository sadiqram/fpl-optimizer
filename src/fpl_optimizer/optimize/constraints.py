"""FPL rules in one place, shared by the single-GW solver, horizon solver, and backtester
(Architecture §4.5). Rules change between seasons — one definition, one place to update.
"""

from __future__ import annotations

SQUAD_SIZE = 15

# 0.1m units = £100.0m. This is the fresh-squad/wildcard budget. Selling price for an
# already-owned squad (purchase price + 50% of any rise, per FPL's actual rule) is a
# strategy-layer concern, not modelled here yet — see Architecture §4.5 "On selling price".
BUDGET = 1000

MAX_PER_TEAM = 3

# element_type id -> (min, max) required count in a 15-man squad. FPL requires exact
# counts, not ranges, but expressing it as a range keeps this usable unchanged if a season
# ever adjusts squad composition.
SQUAD_POSITION_COUNTS = {
    1: (2, 2),  # GKP
    2: (5, 5),  # DEF
    3: (5, 5),  # MID
    4: (3, 3),  # FWD
}

XI_SIZE = 11

# Valid starting-XI formation bounds: exactly 1 GK, and outfield counts that cover every
# legal FPL formation (3-4-3 through 5-4-1 etc.) without allowing an invalid one.
XI_POSITION_MIN = {1: 1, 2: 3, 3: 2, 4: 1}
XI_POSITION_MAX = {1: 1, 2: 5, 3: 5, 4: 3}

# Free-transfer banking cap (M6). Current rule as of the 2024-25 season change noted above
# in BUDGET's comment; was 2 before that. One place to update if it changes again.
FREE_TRANSFER_CAP = 5

# Points deducted per transfer beyond the free ones available that gameweek.
HIT_COST = 4


def selling_price(purchase_price: int, current_price: int) -> int:
    """FPL's actual rule (Architecture §4.5 "On selling price"): a price *drop* is absorbed
    in full (you sell at current_price), but only half of any *rise* is recouped, rounded
    down. Both prices are 0.1m-unit integers already, so `// 2` needs no extra rounding.
    A naive optimizer using current market price for owned players hallucinates budget that
    doesn't exist — this is what prevents that."""
    if current_price <= purchase_price:
        return current_price
    return purchase_price + (current_price - purchase_price) // 2
