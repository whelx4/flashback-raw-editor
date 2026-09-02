from pathlib import Path

from core.config import VIBE_PRESETS, resolve_lut_ref, vibe_config_for
from core.preset_catalog import (
    load_preset_catalog,
    normalize_preset_id,
    picker_items,
    picker_rows,
)


def test_shared_catalog_and_windows_recipes_stay_in_sync():
    catalog = load_preset_catalog()
    assert catalog["schemaVersion"] == 2
    assert len(catalog["presets"]) == 20
    for preset in catalog["presets"]:
        assert preset["id"] in VIBE_PRESETS
        assert preset["status"] in {"provisional", "experimental", "legacy"}
        assert "inspired" in preset["name"]
        path, origin = resolve_lut_ref(vibe_config_for(preset["id"]).lut_ref)
        assert origin == "factory"
        assert Path(path).name == preset["lut"]


def test_legacy_presentation_ids_are_migrated():
    assert normalize_preset_id("disposable") == "funsaver_800"
    assert normalize_preset_id("point_shoot") == "cs2_standard"
    assert normalize_preset_id("funsaver_800") == "funsaver_800"


def test_picker_marks_experimental_rows_and_limits_shortcuts():
    rows = picker_rows()
    assert len(rows) == 20
    assert any("Experimental" in subtitle for _, _, subtitle, _ in rows)
    assert [shortcut for *_, shortcut in rows[:9]] == list("123456789")
    assert all(not shortcut for *_, shortcut in rows[9:])


def test_preset_browser_items_have_compact_grouped_metadata():
    items = picker_items()
    assert len(items) == 20
    assert items[0]["displayName"] == "FunSaver 800"
    assert items[0]["category"] == "Disposable film"
    assert items[0]["name"].endswith("-inspired")
    assert [item["shortcut"] for item in items[:9]] == list("123456789")
