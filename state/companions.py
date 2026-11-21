"""
state/companions.py — Party companion recruitment, dismissal, approval, and camp dialogue responses.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("state_manager")

MAX_PARTY_COMPANIONS = 3


def get_companion_approval(world_state: Dict[str, Any], companion_id: str) -> Dict[str, Any]:
    """
    Spec Module D §2 / Phase 14.2:
    Get or initialize approval state for companion_id in world_state['companions_approval'].
    Baseline approval is 50 (range 0 to 100).
    """
    comp_approval_map = world_state.setdefault("companions_approval", {})
    if companion_id not in comp_approval_map:
        comp_approval_map[companion_id] = {
            "approval": 50,
            "camp_dialogue_pending": True,
            "last_discussion_topic": None,
        }
    return comp_approval_map[companion_id]


def dismiss_companion(npc_id: str, world_state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Spec Section 21 / PART 9:
    Move companion from party.companions to party.former_companions, recording last_location.
    Disallowed during active combat.
    """
    if world_state.get("combat_state") is not None:
        raise ValueError("Cannot dismiss companions during active combat.")

    party = world_state.setdefault("party", {"companions": [], "former_companions": []})
    companions = party.setdefault("companions", [])
    former = party.setdefault("former_companions", [])

    target_idx = None
    target_comp = None
    for idx, comp in enumerate(companions):
        if comp.get("id") == npc_id or comp.get("name") == npc_id:
            target_idx = idx
            target_comp = comp
            break

    if target_comp is None:
        raise ValueError(f"Companion '{npc_id}' is not in active party.")

    companions.pop(target_idx)
    target_comp["last_location"] = world_state.get("current_location", "town_riverside")
    former.append(target_comp)

    logger.info(f"dismiss_companion: '{npc_id}' moved to former_companions at {target_comp['last_location']}.")
    return {"npc_id": npc_id, "dismissed_at": target_comp["last_location"]}


def recruit_companion(companion_data: Dict[str, Any], world_state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recruit an NPC/companion into world_state['party']['companions'].
    Enforces MAX_PARTY_COMPANIONS cap (default 3 active companions).
    Initializes approval state in world_state['companions_approval'].
    """
    party = world_state.setdefault("party", {"companions": [], "former_companions": []})
    companions = party.setdefault("companions", [])
    cid = companion_data.get("id")

    for c in companions:
        if c.get("id") == cid or (cid and c.get("name") == companion_data.get("name")):
            return {"status": "already_in_party", "companion": c}

    if len(companions) >= MAX_PARTY_COMPANIONS:
        logger.warning(
            f"recruit_companion: party already full ({len(companions)}/{MAX_PARTY_COMPANIONS}). "
            f"Cannot recruit '{cid}'."
        )
        return {"status": "party_full", "companion": companion_data}

    former = party.setdefault("former_companions", [])
    party["former_companions"] = [c for c in former if c.get("id") != cid]

    comp_entry = dict(companion_data)
    comp_entry.setdefault("side", "player")
    comp_entry.setdefault("active_conditions", [])
    comp_entry.setdefault("death_saves", {"success": 0, "fail": 0})
    companions.append(comp_entry)

    if cid:
        app_info = get_companion_approval(world_state, cid)
        if "approval" in companion_data:
            app_info["approval"] = companion_data["approval"]

    logger.info(f"recruit_companion: '{companion_data.get('name')}' ({cid}) recruited into active party.")
    return {"status": "recruited", "companion": comp_entry}


def respond_to_camp_dialogue(
    companion_id: str,
    response_type: str,
    world_state: Dict[str, Any],
    dialogue_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Spec Module D §2 / Phase 14.2:
    Process player response (agree, disagree, neutral) to camp companion dialogue.
    """
    comp_state = get_companion_approval(world_state, companion_id)
    old_approval = comp_state.get("approval", 50)

    resp_lower = (response_type or "").strip().lower()
    if resp_lower == "agree":
        delta = 5
    elif resp_lower == "disagree":
        delta = -5
    elif resp_lower == "neutral":
        delta = 0
    else:
        logger.warning(f"Unknown camp response_type '{response_type}', defaulting to 0 delta.")
        delta = 0

    new_approval = max(0, min(100, old_approval + delta))
    comp_state["approval"] = new_approval
    comp_state["camp_dialogue_pending"] = False

    topic = None
    if dialogue_data and isinstance(dialogue_data, dict):
        topic = dialogue_data.get("topic") or dialogue_data.get("statement")
    if topic:
        comp_state["last_discussion_topic"] = topic

    logger.info(
        f"respond_to_camp_dialogue: companion '{companion_id}' {resp_lower} -> "
        f"approval {old_approval} -> {new_approval} (delta={delta:+d})."
    )
    return {
        "companion_id": companion_id,
        "response_type": resp_lower,
        "approval_change": delta,
        "old_approval": old_approval,
        "new_approval": new_approval,
        "camp_dialogue_pending": False,
        "last_discussion_topic": comp_state.get("last_discussion_topic"),
    }
