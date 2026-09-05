"""Shared, presentation-only preset catalog used by Windows and iOS."""

from __future__ import annotations

import json
from functools import lru_cache

from . import resource_path

LEGACY_PRESET_ALIASES = {
    "disposable": "funsaver_800",
    "point_shoot": "cs2_standard",
    "rangefinder": "quicksnap_400",
    "monochrome": "paper_bw",
}


def normalize_preset_id(preset_id: str) -> str:
    """Map removed presentation IDs while keeping old project files readable."""
    return LEGACY_PRESET_ALIASES.get(preset_id, preset_id)


@lru_cache(maxsize=1)
def load_preset_catalog() -> dict:
    path = resource_path("shared/presets.json")
    with open(path, "r", encoding="utf-8") as handle:
        catalog = json.load(handle)
    if catalog.get("schemaVersion") not in (1, 2) or not catalog.get("presets"):
        raise ValueError("Unsupported or empty preset catalog")
    return catalog


def picker_rows() -> list[tuple[str, str, str, str]]:
    """Return rows in the compact form expected by ``VibePicker``."""
    return [
        (preset["id"], preset["name"],
         preset["subtitle"] + (" · Experimental" if preset.get("status") == "experimental" else ""),
         str(index) if index <= 9 else "")
        for index, preset in enumerate(load_preset_catalog()["presets"], start=1)
    ]


def picker_items() -> list[dict]:
    """Return presentation metadata for the full preset browser.

    The catalog keeps the legally explicit ``-inspired`` product names.  The
    picker can afford a separate, shorter display name because it also shows
    an Inspired badge and the full catalog name in a tooltip.
    """
    items = []
    for index, preset in enumerate(load_preset_catalog()["presets"], start=1):
        item = dict(preset)
        item["displayName"] = preset.get(
            "displayName", preset["name"].removesuffix("-inspired")
        )
        item["shortcut"] = str(index) if index <= 9 else ""
        items.append(item)
    return items
