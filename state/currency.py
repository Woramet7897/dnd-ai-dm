"""
state/currency.py — Tri-Denomination Currency System (Gold / Silver / Copper).
D&D 5e standard: 1 GP = 10 SP = 100 CP.
"""

import math
from typing import Any, Dict

CP_PER_SP = 10
SP_PER_GP = 10
CP_PER_GP = CP_PER_SP * SP_PER_GP  # 100


def _ensure_currency(state: Dict[str, Any]) -> Dict[str, int]:
    """
    Ensure player state has a 'currency' dict {gp, sp, cp}.
    Default initial purse: {'gp': 10, 'sp': 5, 'cp': 30}.
    Migrates legacy 'gold' integer if present and keeps 'gold' in sync.
    """
    if "currency" not in state or not isinstance(state["currency"], dict):
        legacy_gold = state.get("gold", 10)
        state["currency"] = {"gp": int(legacy_gold), "sp": 5, "cp": 30}
    cur = state["currency"]
    cur.setdefault("gp", 10)
    cur.setdefault("sp", 5)
    cur.setdefault("cp", 30)

    # Sync if 'gold' was updated directly in tests/external assignment
    if "gold" in state and state["gold"] != cur["gp"]:
        cur["gp"] = int(state["gold"])
    else:
        state["gold"] = cur["gp"]
    return cur


def get_total_copper(state: Dict[str, Any]) -> int:
    """Return the player's total wealth converted to copper pieces."""
    cur = _ensure_currency(state)
    return cur["gp"] * CP_PER_GP + cur["sp"] * CP_PER_SP + cur["cp"]


def _from_copper(total_cp: int) -> Dict[str, int]:
    """Convert a total copper amount into {gp, sp, cp} breakdown."""
    total_cp = max(0, total_cp)
    gp = total_cp // CP_PER_GP
    remainder = total_cp % CP_PER_GP
    sp = remainder // CP_PER_SP
    cp = remainder % CP_PER_SP
    return {"gp": gp, "sp": sp, "cp": cp}


def can_afford(state: Dict[str, Any], *, gp: int = 0, sp: int = 0, cp: int = 0) -> bool:
    """Check if the player can afford a cost specified in mixed denominations."""
    cost_cp = gp * CP_PER_GP + sp * CP_PER_SP + cp
    return get_total_copper(state) >= cost_cp


def add_currency(state: Dict[str, Any], *, gp: int = 0, sp: int = 0, cp: int = 0) -> Dict[str, int]:
    """Add coins to the player's purse. Returns the updated currency dict."""
    cur = _ensure_currency(state)
    cur["gp"] += gp
    cur["sp"] += sp
    cur["cp"] += cp
    state["gold"] = cur["gp"]  # keep legacy key in sync
    return cur


def subtract_currency(state: Dict[str, Any], *, gp: int = 0, sp: int = 0, cp: int = 0) -> bool:
    """
    Subtract a cost from the player's purse using smart auto-change.
    Directly deducts matching denominations when sufficient.
    Otherwise converts from total copper and redistributes.
    Returns True if successful, False if insufficient funds.
    """
    _ensure_currency(state)
    cur = state["currency"]
    cost_cp = gp * CP_PER_GP + sp * CP_PER_SP + cp
    total = get_total_copper(state)
    if total < cost_cp:
        return False

    # Exact direct deduction if player has sufficient coins of each requested denomination
    if cur["gp"] >= gp and cur["sp"] >= sp and cur["cp"] >= cp:
        cur["gp"] -= gp
        cur["sp"] -= sp
        cur["cp"] -= cp
    else:
        new_cur = _from_copper(total - cost_cp)
        state["currency"] = new_cur
    state["gold"] = state["currency"]["gp"]  # keep legacy key in sync
    return True


def format_currency(state_or_dict: Dict[str, Any]) -> str:
    """
    Format currency for display. Accepts either a player state or a currency dict.
    Returns e.g. '10 GP, 5 SP, 30 CP'. NO EMOJI.
    """
    if not isinstance(state_or_dict, dict):
        return "0 CP"
    if "currency" in state_or_dict and isinstance(state_or_dict["currency"], dict):
        cur = state_or_dict["currency"]
    elif "gp" in state_or_dict or "sp" in state_or_dict or "cp" in state_or_dict:
        cur = state_or_dict
    else:
        cur = {"gp": state_or_dict.get("gold", 10), "sp": 5, "cp": 30}

    parts = []
    gp = cur.get("gp", 0)
    sp = cur.get("sp", 0)
    cp = cur.get("cp", 0)
    if gp > 0 or (sp == 0 and cp == 0):
        parts.append(f"{gp} GP")
    if sp > 0:
        parts.append(f"{sp} SP")
    if cp > 0:
        parts.append(f"{cp} CP")
    return ", ".join(parts) if parts else "0 CP"


def gold_to_mixed_currency(gp_amount: int) -> Dict[str, int]:
    """Convert a legacy GP integer into a mixed denomination dict."""
    return {"gp": gp_amount, "sp": 0, "cp": 0}


def get_item_price_breakdown(item_info: Dict[str, Any], multiplier: float = 1.0) -> Dict[str, Any]:
    """
    Calculate buy cost breakdown based on currency denomination.
    Priority: price_cp -> price_sp -> value_gold (GP).
    Returns dict: {'gp': int, 'sp': int, 'cp': int, 'text': str, 'total_cp': int}
    """
    if "price_cp" in item_info:
        cp = math.ceil(item_info["price_cp"] * multiplier)
        return {"gp": 0, "sp": 0, "cp": cp, "text": f"{cp} CP", "total_cp": cp}
    elif "price_sp" in item_info:
        sp = math.ceil(item_info["price_sp"] * multiplier)
        return {"gp": 0, "sp": sp, "cp": 0, "text": f"{sp} SP", "total_cp": sp * CP_PER_SP}
    else:
        gp = math.ceil(item_info.get("value_gold", 0) * multiplier)
        return {"gp": gp, "sp": 0, "cp": 0, "text": f"{gp} GP", "total_cp": gp * CP_PER_GP}


def get_item_sell_breakdown(item_info: Dict[str, Any], multiplier: float = 0.5) -> Dict[str, Any]:
    """
    Calculate sell gain breakdown based on currency denomination.
    Returns dict: {'gp': int, 'sp': int, 'cp': int, 'text': str}
    """
    if "price_cp" in item_info:
        cp = math.floor(item_info["price_cp"] * multiplier)
        return {"gp": 0, "sp": 0, "cp": cp, "text": f"{cp} CP"}
    elif "price_sp" in item_info:
        sp = math.floor(item_info["price_sp"] * multiplier)
        return {"gp": 0, "sp": sp, "cp": 0, "text": f"{sp} SP"}
    else:
        gp = math.floor(item_info.get("value_gold", 0) * multiplier)
        return {"gp": gp, "sp": 0, "cp": 0, "text": f"{gp} GP"}
