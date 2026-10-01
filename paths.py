"""
paths.py — Central directory and file path configuration.
Pure standard library only. No project module imports.
"""

import os

# Root directory of the repository
BASE_DIR: str = os.path.dirname(os.path.abspath(__file__))

# Primary directories
DATA_DIR: str = os.path.join(BASE_DIR, "data")
CATALOG_DIR: str = os.path.join(DATA_DIR, "catalogs")
DOCS_DIR: str = os.path.join(BASE_DIR, "docs")
SAVES_DIR: str = os.path.join(BASE_DIR, "saves")
WORLD_SAVES_DIR: str = os.path.join(BASE_DIR, "world_saves")
BACKUP_DIR: str = os.path.join(BASE_DIR, "save_backups")
