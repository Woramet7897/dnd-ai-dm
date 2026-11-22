"""
validators/extraction.py — Field-level whitelist validation for LLM extraction output.
"""

import logging
import uuid
from typing import Any, Dict, List, Optional

from validators.entities import (
    validate_generated_event,
    validate_generated_item,
    validate_generated_monster,
    validate_generated_npc,
    validate_generated_spell,
)

logger = logging.getLogger("validation")

ACTION_TAGS: List[str] = [
    "honesty", "kindness", "curiosity", "humor",
    "cruelty", "greed", "cowardice", "bravery", "flirtation",
]

NUMERIC_BOUNDS: Dict[str, tuple] = {
    "hp_change": (-200, 200),
    "npc_relationship_change_delta": (-50, 50),
}

VALID_QUEST_STATUSES: set = {"active", "completed", "failed", "abandoned"}


def validate_spell_id(spell_id: Any, world_state: Optional[Dict] = None) -> bool:
    """Return True if spell_id exists in spell_catalog.json OR generated_spells in world_state."""
    if not isinstance(spell_id, str):
        logger.debug(f"spell_id is not a string (got {type(spell_id).__name__}: {spell_id!r}) — invalid.")
        return False

    import validation
    catalog = validation._get_spell_catalog()
    if spell_id in catalog:
        return True

    if world_state and isinstance(world_state, dict):
        gen_spells = world_state.get("generated_spells", {})
        if spell_id in gen_spells:
            return True

    logger.debug(f"Unknown spell_id '{spell_id}' — not in spell_catalog.json or generated_spells, dropped.")
    return False


def validate_action_tags(tags: Any) -> List[str]:
    """Drop any tag not in the fixed ACTION_TAGS vocabulary."""
    if not isinstance(tags, list):
        logger.debug(f"action_tags is not a list (got {type(tags).__name__}) — dropping entire field.")
        return []

    valid = []
    for tag in tags:
        if not isinstance(tag, str):
            logger.debug(f"action_tag entry is not a string: {tag!r} — dropped.")
            continue
        if tag in ACTION_TAGS:
            valid.append(tag)
        else:
            logger.debug(f"Unknown action_tag '{tag}' — not in fixed vocabulary, dropped.")
    return valid


def validate_quest_updates(quest_updates: Any) -> Optional[Dict]:
    """Validate and whitelist the quest_updates block from LLM extraction output."""
    if not isinstance(quest_updates, dict):
        logger.debug(f"quest_updates is not a dict ({type(quest_updates).__name__}) — dropped.")
        return None

    cleaned: Dict = {}

    if "new_quest" in quest_updates:
        nq = quest_updates["new_quest"]
        if isinstance(nq, dict):
            nq_clean: Dict = {}
            title = nq.get("title")
            if isinstance(title, str) and title.strip():
                nq_clean["title"] = title.strip()
            else:
                logger.debug("quest_updates.new_quest.title missing or not a string — dropped.")
            quest_type = nq.get("quest_type", "side")
            if quest_type not in ("main", "side"):
                logger.debug(f"quest_updates.new_quest.quest_type '{quest_type}' invalid — defaulting to 'side'.")
                quest_type = "side"
            nq_clean["quest_type"] = quest_type
            desc = nq.get("description")
            if isinstance(desc, str):
                nq_clean["description"] = desc
            if "title" in nq_clean:
                cleaned["new_quest"] = nq_clean
            else:
                logger.debug("quest_updates.new_quest had no valid title — dropped.")
        else:
            logger.debug(f"quest_updates.new_quest not a dict ({type(nq).__name__}) — dropped.")

    if "objective_update" in quest_updates:
        ou = quest_updates["objective_update"]
        if isinstance(ou, dict):
            ou_clean: Dict = {}
            qid = ou.get("quest_id")
            if isinstance(qid, str) and qid.strip():
                ou_clean["quest_id"] = qid.strip()
            else:
                logger.debug("quest_updates.objective_update.quest_id missing or not a string — dropped.")
            obj_idx = ou.get("objective_index")
            if isinstance(obj_idx, int) and obj_idx >= 0:
                ou_clean["objective_index"] = obj_idx
            else:
                logger.debug(f"quest_updates.objective_update.objective_index invalid ({obj_idx!r}) — dropped.")
            done = ou.get("done")
            if isinstance(done, bool):
                ou_clean["done"] = done
            else:
                logger.debug(f"quest_updates.objective_update.done not a bool ({done!r}) — dropped.")
            if "quest_id" in ou_clean and "objective_index" in ou_clean and "done" in ou_clean:
                cleaned["objective_update"] = ou_clean
            else:
                logger.debug("quest_updates.objective_update missing required fields — dropped.")
        else:
            logger.debug(f"quest_updates.objective_update not a dict ({type(ou).__name__}) — dropped.")

    if not cleaned:
        logger.debug("quest_updates had no valid fields after filtering — dropped.")
        return None

    return cleaned


def validate_item_id(item_id: Any, world_state: Optional[Dict] = None) -> bool:
    """Return True if item_id exists in item_catalog.json OR generated_items in world_state."""
    if not isinstance(item_id, str):
        logger.debug(f"item_id is not a string (got {type(item_id).__name__}: {item_id!r}) — invalid.")
        return False

    import validation
    catalog = validation._get_item_catalog()
    if item_id in catalog:
        return True

    if world_state and isinstance(world_state, dict):
        gen_items = world_state.get("generated_items", {})
        if item_id in gen_items:
            return True

    logger.debug(f"Unknown item_id '{item_id}' — not in item_catalog.json or generated_items, dropped.")
    return False


def validate_monster_ids(enemy_ids: Any, world_state: Optional[Dict] = None) -> List[str]:
    """Validate a list of monster IDs against monster_catalog.json and world_state["generated_monsters"]."""
    if not isinstance(enemy_ids, list):
        logger.debug(f"enemy_ids is not a list (got {type(enemy_ids).__name__}) — returning empty.")
        return []

    import validation
    catalog = validation._get_monster_catalog()
    gen_monsters = world_state.get("generated_monsters", {}) if isinstance(world_state, dict) else {}
    valid = []
    for mid in enemy_ids:
        if not isinstance(mid, str):
            logger.debug(f"monster_id entry is not a string: {mid!r} — dropped.")
            continue
        if mid in catalog:
            valid.append(mid)
        elif mid in gen_monsters:
            valid.append(mid)
        else:
            logger.debug(f"Unknown monster_id '{mid}' — not in monster_catalog.json or generated_monsters, dropped.")
    return valid


def validate_npc_id(npc_id: Any, world_state: Dict) -> bool:
    """Return True if npc_id exists in npc_catalog.json, room npcs, npc_relationships, party.companions, or generated_npcs."""
    if not isinstance(npc_id, str):
        logger.debug(f"npc_id is not a string (got {type(npc_id).__name__}: {npc_id!r}) — invalid.")
        return False

    import validation
    npc_cat = validation._get_npc_catalog()
    if npc_id in npc_cat:
        return True

    npc_rels = world_state.get("npc_relationships", {}) if isinstance(world_state, dict) else {}
    if npc_id in npc_rels:
        return True

    party = world_state.get("party", {}) if isinstance(world_state, dict) else {}
    companions = party.get("companions", [])
    companion_ids = {c.get("id") for c in companions if isinstance(c, dict)}
    if npc_id in companion_ids:
        return True

    gen_npcs = world_state.get("generated_npcs", {}) if isinstance(world_state, dict) else {}
    if npc_id in gen_npcs:
        return True

    logger.debug(f"Unknown npc_id '{npc_id}' — not in npc_catalog.json, npc_relationships, party.companions, or generated_npcs, dropped.")
    return False


def validate_numeric_range(field_name: str, value: Any, min_val: int, max_val: int) -> Optional[int]:
    """Check value is an int/float within [min_val, max_val]. Out-of-range values are DROPPED."""
    if not isinstance(value, (int, float)):
        logger.debug(f"Field '{field_name}' is not numeric (got {type(value).__name__}: {value!r}) — dropped.")
        return None

    int_value = int(value)
    if int_value < min_val or int_value > max_val:
        logger.debug(
            f"Field '{field_name}' value {int_value} is outside allowed range "
            f"[{min_val}, {max_val}] — dropped (not clamped)."
        )
        return None

    return int_value


def validate_extraction_output(
    raw: Any,
    world_state: Optional[Dict] = None,
    player_state: Optional[Dict] = None,
) -> Dict:
    """Top-level entry point. Runs every sub-validator field by field on the raw extraction output."""
    try:
        import validation
        return validation._validate_extraction_output_inner(raw, world_state=world_state, player_state=player_state)
    except Exception as ex:
        logger.error(f"validate_extraction_output failed: {ex}")
        return {}


def _validate_extraction_output_inner(
    raw: Any,
    world_state: Optional[Dict] = None,
    player_state: Optional[Dict] = None,
) -> Dict:
    import validation
    if world_state is None:
        world_state = {}

    if not isinstance(raw, dict):
        logger.debug(f"Extraction output is not a dict (got {type(raw).__name__}) — returning empty.")
        return {}

    cleaned: Dict = {}

    # ── generated_monsters ────────────────────────────────────────────────────
    alias_to_real_mon_id: Dict[str, str] = {}
    if "generated_monsters" in raw and isinstance(raw["generated_monsters"], dict):
        catalog = validation._get_monster_catalog()
        p_lvl = 1
        if player_state and isinstance(player_state, dict):
            p_lvl = player_state.get("level", 1)

        for alias, m_data in raw["generated_monsters"].items():
            if not isinstance(alias, str) or not alias.strip():
                continue
            alias_clean = alias.strip().lower()

            if alias_clean in catalog:
                logger.warning(
                    f"generated_monsters rejected override of static catalog monster '{alias_clean}'."
                )
                continue

            valid_m = validate_generated_monster(m_data, player_level=p_lvl)
            if valid_m is None:
                logger.debug(f"generated_monster '{alias}' failed validation — dropped.")
                continue

            real_id = f"gen_mon_{uuid.uuid4().hex[:8]}"
            valid_m["id"] = real_id

            if world_state is not None and isinstance(world_state, dict):
                gen_monsters = world_state.setdefault("generated_monsters", {})
                gen_monsters[real_id] = valid_m
                logger.info(f"Registered dynamic monster '{valid_m['name']}' as '{real_id}' in world_state.")

            alias_to_real_mon_id[alias] = real_id
            alias_to_real_mon_id[alias_clean] = real_id

    # ── generated_items ───────────────────────────────────────────────────────
    alias_to_real_item_id: Dict[str, str] = {}
    if "generated_items" in raw and isinstance(raw["generated_items"], dict):
        item_catalog = validation._get_item_catalog()
        for alias, i_data in raw["generated_items"].items():
            if not isinstance(alias, str) or not alias.strip():
                continue
            alias_clean = alias.strip().lower()

            if alias_clean in item_catalog:
                logger.warning(
                    f"generated_items rejected override of static catalog item '{alias_clean}'."
                )
                continue

            valid_item = validate_generated_item(i_data)
            if valid_item is None:
                logger.debug(f"generated_item '{alias}' failed validation — dropped.")
                continue

            real_id = f"gen_item_{uuid.uuid4().hex[:8]}"
            valid_item["item_id"] = real_id

            if world_state is not None and isinstance(world_state, dict):
                gen_items = world_state.setdefault("generated_items", {})
                gen_items[real_id] = valid_item
                logger.info(f"Registered dynamic item '{valid_item['name']}' as '{real_id}' in world_state.")

            alias_to_real_item_id[alias] = real_id
            alias_to_real_item_id[alias_clean] = real_id

    # ── Pre-validate world_updates.new_location so move_to_location_id & generated_events can reference it! ──
    validated_new_location: Optional[Dict[str, Any]] = None
    if "world_updates" in raw and isinstance(raw["world_updates"], dict):
        wu = raw["world_updates"]
        if "new_location" in wu and isinstance(wu["new_location"], dict):
            dm = validation._get_dungeon_manager()
            nl_candidate = dm.normalize_new_location(wu["new_location"], world_state) if hasattr(dm, "normalize_new_location") else wu["new_location"]
            # Remap dynamic loot item aliases and dynamic encounter monster aliases inside new_location!
            if isinstance(nl_candidate.get("loot"), list):
                remapped_loot = []
                for lit in nl_candidate["loot"]:
                    if isinstance(lit, dict) and isinstance(lit.get("item_id"), str):
                        raw_lid = lit["item_id"]
                        real_lid = alias_to_real_item_id.get(raw_lid, alias_to_real_item_id.get(raw_lid.lower(), raw_lid))
                        lit_copy = dict(lit)
                        lit_copy["item_id"] = real_lid
                        remapped_loot.append(lit_copy)
                    elif isinstance(lit, str):
                        real_lid = alias_to_real_item_id.get(lit, alias_to_real_item_id.get(lit.lower(), lit))
                        remapped_loot.append({"item_id": real_lid, "quantity": 1})
                nl_candidate["loot"] = remapped_loot
            if isinstance(nl_candidate.get("encounter_table"), list):
                remapped_enc = []
                for em in nl_candidate["encounter_table"]:
                    if isinstance(em, str):
                        remapped_enc.append(alias_to_real_mon_id.get(em, alias_to_real_mon_id.get(em.lower(), em)))
                if remapped_enc:
                    nl_candidate["encounter_table"] = remapped_enc
            ok, _ = dm.validate_new_location(nl_candidate, world_state)
            if ok:
                validated_new_location = nl_candidate

    # ── generated_npcs ────────────────────────────────────────────────────────
    alias_to_real_npc_id: Dict[str, str] = {}
    if "generated_npcs" in raw and isinstance(raw["generated_npcs"], dict):
        p_lvl = 1
        if player_state and isinstance(player_state, dict):
            p_lvl = player_state.get("level", 1)

        party_comp_ids = set()
        if world_state and isinstance(world_state, dict):
            party_comp_ids = {
                c.get("id") for c in world_state.get("party", {}).get("companions", [])
                if isinstance(c, dict) and c.get("id")
            }

        for alias, n_data in raw["generated_npcs"].items():
            if not isinstance(alias, str) or not alias.strip():
                continue
            alias_clean = alias.strip().lower()

            if alias_clean in party_comp_ids:
                logger.warning(
                    f"generated_npcs rejected override of existing companion '{alias_clean}'."
                )
                continue

            valid_npc = validate_generated_npc(n_data, player_level=p_lvl)
            if valid_npc is None:
                logger.debug(f"generated_npc '{alias}' failed validation — dropped.")
                continue

            real_id = f"gen_npc_{uuid.uuid4().hex[:8]}"
            valid_npc["id"] = real_id

            if world_state is not None and isinstance(world_state, dict):
                gen_npcs = world_state.setdefault("generated_npcs", {})
                gen_npcs[real_id] = valid_npc
                # Also attach NPC name to current_location room's npcs list if present so Pickpocket / room NPC UI sees them!
                try:
                    dm = validation._get_dungeon_manager()
                    cur_room = dm.get_current_room(world_state)
                    if isinstance(cur_room, dict):
                        cur_loc_id = world_state.get("current_location")
                        dyn_rooms = world_state.setdefault("dynamic_rooms", {})
                        if cur_loc_id and cur_loc_id not in dyn_rooms:
                            import copy
                            cur_room = copy.deepcopy(cur_room)
                            dyn_rooms[cur_loc_id] = cur_room
                        r_npcs = cur_room.setdefault("npcs", [])
                        if valid_npc["name"] not in r_npcs:
                            r_npcs.append(valid_npc["name"])
                except Exception:
                    pass
                logger.info(f"Registered dynamic NPC '{valid_npc['name']}' as '{real_id}' in world_state.")

            alias_to_real_npc_id[alias] = real_id
            alias_to_real_npc_id[alias_clean] = real_id

    # ── generated_events ──────────────────────────────────────────────────────
    alias_to_real_event_id: Dict[str, str] = {}
    if "generated_events" in raw and isinstance(raw["generated_events"], dict):
        dm = validation._get_dungeon_manager()
        for alias, e_data in raw["generated_events"].items():
            if not isinstance(alias, str) or not alias.strip():
                continue
            alias_clean = alias.strip().lower()

            valid_event = validate_generated_event(e_data, world_state=world_state)
            if valid_event is None:
                logger.debug(f"generated_event '{alias}' failed validation — dropped.")
                continue

            real_id = dm.register_dynamic_event(valid_event, world_state if world_state else {})
            alias_to_real_event_id[alias] = real_id
            alias_to_real_event_id[alias_clean] = real_id

    # ── generated_spells ──────────────────────────────────────────────────────
    alias_to_real_spell_id: Dict[str, str] = {}
    if "generated_spells" in raw and isinstance(raw["generated_spells"], dict):
        spell_cat = validation._get_spell_catalog()
        p_lvl = 1
        if player_state and isinstance(player_state, dict):
            p_lvl = player_state.get("level", 1)

        for alias, s_data in raw["generated_spells"].items():
            if not isinstance(alias, str) or not alias.strip():
                continue
            alias_clean = alias.strip().lower()

            if alias_clean in spell_cat:
                logger.warning(
                    f"generated_spells rejected override of static catalog spell '{alias_clean}'."
                )
                continue

            valid_spell = validate_generated_spell(s_data, player_level=p_lvl)
            if valid_spell is None:
                logger.debug(f"generated_spell '{alias}' failed validation — dropped.")
                continue

            real_id = f"gen_spell_{uuid.uuid4().hex[:8]}"
            valid_spell["spell_id"] = real_id

            if world_state is not None and isinstance(world_state, dict):
                gen_spells = world_state.setdefault("generated_spells", {})
                gen_spells[real_id] = valid_spell
                logger.info(f"Registered dynamic spell '{valid_spell['name']}' as '{real_id}' in world_state.")

            alias_to_real_spell_id[alias] = real_id
            alias_to_real_spell_id[alias_clean] = real_id

    # ── state_updates ──────────────────────────────────────────────────────────
    if "state_updates" in raw:
        su_raw = raw["state_updates"]
        if isinstance(su_raw, dict):
            su_clean: Dict = {}

            if "hp_change" in su_raw:
                hp_min, hp_max = NUMERIC_BOUNDS["hp_change"]
                result = validate_numeric_range("hp_change", su_raw["hp_change"], hp_min, hp_max)
                if result is not None:
                    su_clean["hp_change"] = result
                else:
                    logger.debug("hp_change dropped from state_updates.")

            if "add_item_id" in su_raw:
                raw_iid = su_raw["add_item_id"]
                target_iid = alias_to_real_item_id.get(
                    raw_iid, alias_to_real_item_id.get(raw_iid.lower(), raw_iid)
                ) if isinstance(raw_iid, str) else raw_iid
                if validate_item_id(target_iid, world_state):
                    su_clean["add_item_id"] = target_iid

            if "remove_item_id" in su_raw:
                raw_iid = su_raw["remove_item_id"]
                target_iid = alias_to_real_item_id.get(
                    raw_iid, alias_to_real_item_id.get(raw_iid.lower(), raw_iid)
                ) if isinstance(raw_iid, str) else raw_iid
                if validate_item_id(target_iid, world_state):
                    su_clean["remove_item_id"] = target_iid

            if "gold_change" in su_raw:
                result = validate_numeric_range("gold_change", su_raw["gold_change"], -10000, 10000)
                if result is not None:
                    su_clean["gold_change"] = result

            if "recruit_companion_id" in su_raw or "add_companion_id" in su_raw:
                raw_cid = su_raw.get("recruit_companion_id") or su_raw.get("add_companion_id")
                target_cid = alias_to_real_npc_id.get(
                    raw_cid, alias_to_real_npc_id.get(str(raw_cid).lower(), raw_cid)
                ) if isinstance(raw_cid, str) else raw_cid
                if validate_npc_id(target_cid, world_state):
                    su_clean["recruit_companion_id"] = target_cid

            if "learn_spell_id" in su_raw or "add_spell_id" in su_raw:
                raw_sid = su_raw.get("learn_spell_id") or su_raw.get("add_spell_id")
                target_sid = alias_to_real_spell_id.get(
                    raw_sid, alias_to_real_spell_id.get(str(raw_sid).lower(), raw_sid)
                ) if isinstance(raw_sid, str) else raw_sid
                if validate_spell_id(target_sid, world_state):
                    su_clean["learn_spell_id"] = target_sid

            if "move_to_location_id" in su_raw:
                dest_id = su_raw["move_to_location_id"]
                if isinstance(dest_id, str) and dest_id.strip():
                    dm = validation._get_dungeon_manager()
                    known = set(dm._all_known_room_ids(world_state if world_state else {}))
                    if validated_new_location and isinstance(validated_new_location.get("id"), str):
                        known.add(validated_new_location["id"])
                    if dest_id in known:
                        su_clean["move_to_location_id"] = dest_id
                    else:
                        logger.debug(
                            f"state_updates.move_to_location_id '{dest_id}' not in known rooms — dropped."
                        )
                else:
                    logger.debug("state_updates.move_to_location_id not a non-empty string — dropped.")

            if su_clean:
                cleaned["state_updates"] = su_clean
        else:
            logger.debug(f"state_updates is not a dict (got {type(su_raw).__name__}) — dropped.")

    # ── requires_roll ─────────────────────────────────────────────────────────
    if "requires_roll" in raw:
        rr = raw["requires_roll"]
        VALID_D = {"easy", "medium", "hard", "very_hard"}
        VALID_S = {"STR", "DEX", "CON", "INT", "WIS", "CHA"}
        if isinstance(rr, dict):
            d, s = rr.get("difficulty"), rr.get("stat")
            if d not in VALID_D:
                logger.debug(f"requires_roll.difficulty '{d}' invalid — dropped.")
            elif s not in VALID_S:
                logger.debug(f"requires_roll.stat '{s}' invalid — dropped.")
            else:
                cleaned["requires_roll"] = {"difficulty": d, "stat": s}
        else:
            logger.debug("requires_roll not a dict — dropped.")

    # ── combat_start ──────────────────────────────────────────────────────────
    if "combat_start" in raw:
        cs = raw["combat_start"]
        if isinstance(cs, dict) and "enemies" in cs:
            raw_enemies = cs.get("enemies", [])
            remapped_enemies = []
            if isinstance(raw_enemies, list):
                for e in raw_enemies:
                    if isinstance(e, str):
                        remapped_enemies.append(alias_to_real_mon_id.get(e, alias_to_real_mon_id.get(e.lower(), e)))
                    else:
                        remapped_enemies.append(e)
            else:
                remapped_enemies = raw_enemies

            valid_enemies = validate_monster_ids(remapped_enemies, world_state=world_state)
            if valid_enemies:
                cleaned["combat_start"] = {"enemies": valid_enemies}
            else:
                logger.debug("combat_start.enemies had no valid monster IDs — combat_start dropped.")
        else:
            logger.debug("combat_start malformed (no 'enemies' key or not a dict) — dropped.")

    # ── world_updates ─────────────────────────────────────────────────────────
    if "world_updates" in raw:
        wu = raw["world_updates"]
        if isinstance(wu, dict):
            wu_clean = dict(wu)
            if "new_location" in wu_clean:
                if validated_new_location is not None:
                    wu_clean["new_location"] = validated_new_location
                else:
                    nl = wu_clean["new_location"]
                    dm = validation._get_dungeon_manager()
                    ok, reason = dm.validate_new_location(nl, world_state)
                    if not ok:
                        logger.debug(
                            f"world_updates.new_location failed validation — stripped from world_updates. "
                            f"Reason: {reason}"
                        )
                        del wu_clean["new_location"]
            if wu_clean:
                cleaned["world_updates"] = wu_clean
        else:
            logger.debug("world_updates is not a dict — dropped.")

    # ── quest_updates ─────────────────────────────────────────────────────────
    if "quest_updates" in raw:
        qu_clean = validate_quest_updates(raw["quest_updates"])
        if qu_clean is not None:
            cleaned["quest_updates"] = qu_clean

    # ── action_tags ───────────────────────────────────────────────────────────
    if "action_tags" in raw:
        valid_tags = validate_action_tags(raw["action_tags"])
        if valid_tags:
            cleaned["action_tags"] = valid_tags

    # ── npc_relationship_change ───────────────────────────────────────────────
    if "npc_relationship_change" in raw:
        nrc = raw["npc_relationship_change"]
        if isinstance(nrc, dict):
            raw_nid = nrc.get("npc_id")
            target_nid = alias_to_real_npc_id.get(
                raw_nid, alias_to_real_npc_id.get(str(raw_nid).lower(), raw_nid)
            ) if isinstance(raw_nid, str) else raw_nid
            delta = nrc.get("delta")

            npc_ok = validate_npc_id(target_nid, world_state)
            d_min, d_max = NUMERIC_BOUNDS["npc_relationship_change_delta"]
            delta_ok = validate_numeric_range("npc_relationship_change.delta", delta, d_min, d_max)

            if npc_ok and delta_ok is not None:
                cleaned["npc_relationship_change"] = {"npc_id": target_nid, "delta": delta_ok}
            else:
                logger.debug("npc_relationship_change dropped (bad npc_id or delta out of range).")
        else:
            logger.debug("npc_relationship_change is not a dict — dropped.")

    return cleaned
