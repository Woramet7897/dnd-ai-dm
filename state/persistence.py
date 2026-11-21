"""
state/persistence.py — Atomic JSON save/load and rolling backup rotation.
"""

import json
import os
import tempfile
from typing import Any, Dict

import state_manager


def _save_path(character_name: str) -> str:
    return os.path.join(state_manager.SAVES_DIR, f"{character_name}.json")


def _world_save_path(character_name: str) -> str:
    return os.path.join(state_manager.WORLD_SAVES_DIR, f"{character_name}_world.json")


def _backup_dir(character_name: str) -> str:
    return os.path.join(state_manager.BACKUP_DIR, character_name)


def load_character(character_name: str) -> Dict[str, Any]:
    """
    Load a character save. Raises ValueError on schema version mismatch
    rather than crashing obscurely (spec Section 5a).
    """
    path = _save_path(character_name)
    if not os.path.exists(path):
        raise FileNotFoundError(f"No save found for '{character_name}' at {path}")
    with open(path, "r", encoding="utf-8") as f:
        state = json.load(f)
    version = state.get("schema_version")
    if version != state_manager.SUPPORTED_SCHEMA_VERSION:
        raise ValueError(
            f"Save file schema_version={version} is not supported. "
            f"This engine requires schema_version={state_manager.SUPPORTED_SCHEMA_VERSION}. "
            f"Do not try to load old saves — the format changed."
        )
    return state


def load_world(character_name: str) -> Dict[str, Any]:
    """Load world state. Raises ValueError on schema version mismatch."""
    path = _world_save_path(character_name)
    if not os.path.exists(path):
        raise FileNotFoundError(f"No world save found for '{character_name}' at {path}")
    with open(path, "r", encoding="utf-8") as f:
        world = json.load(f)
    version = world.get("schema_version")
    if version != state_manager.SUPPORTED_SCHEMA_VERSION:
        raise ValueError(
            f"World save schema_version={version} not supported. "
            f"Requires schema_version={state_manager.SUPPORTED_SCHEMA_VERSION}."
        )
    return world


def _atomic_write(path: str, data: Dict[str, Any]):
    """Write JSON atomically using a temp file + os.replace() (spec Section 5c)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    dir_ = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _rotate_backup(character_name: str, state: Dict[str, Any]):
    """Keep last MAX_BACKUPS autosaves in save_backups/<name>/ (spec Section 5d)."""
    max_backups = state_manager.MAX_BACKUPS
    backup_dir = _backup_dir(character_name)
    os.makedirs(backup_dir, exist_ok=True)
    for i in range(max_backups - 1, 0, -1):
        src = os.path.join(backup_dir, f"backup_{i}.json")
        dst = os.path.join(backup_dir, f"backup_{i + 1}.json")
        if os.path.exists(src):
            if i + 1 > max_backups:
                os.unlink(src)
            else:
                os.replace(src, dst)
    _atomic_write(os.path.join(backup_dir, "backup_1.json"), state)


def save_character(character_name: str, state: Dict[str, Any]):
    """Atomic save + rolling backup rotation."""
    _rotate_backup(character_name, state)
    _atomic_write(_save_path(character_name), state)


def save_world(character_name: str, world: Dict[str, Any]):
    """Atomic world save (no backup rotation — backup covers char sheet only)."""
    _atomic_write(_world_save_path(character_name), world)
