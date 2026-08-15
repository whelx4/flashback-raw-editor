"""Source/resource validation that can run on Windows without Xcode."""
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
IOS = ROOT / "ios" / "LoFiLogicIOS"


def test_all_swift_sources_parse_without_syntax_errors():
    tree_sitter = pytest.importorskip("tree_sitter")
    swift = pytest.importorskip("tree_sitter_swift")
    parser = tree_sitter.Parser(tree_sitter.Language(swift.language()))
    sources = sorted(IOS.rglob("*.swift"))
    assert sources
    failures = [str(path.relative_to(ROOT)) for path in sources
                if parser.parse(path.read_bytes()).root_node.has_error]
    assert not failures


def test_ios_bundle_declares_shared_catalog_and_luts():
    project = (IOS / "project.yml").read_text(encoding="utf-8")
    assert "../../shared/presets.json" in project
    assert "../../assets/luts" in project
    catalog = json.loads((ROOT / "shared" / "presets.json").read_text(encoding="utf-8"))
    missing = [preset["lut"] for preset in catalog["presets"]
               if not (ROOT / "assets" / "luts" / preset["lut"]).is_file()]
    assert not missing
    missing_ios = [f"ios_{preset['id']}.cube" for preset in catalog["presets"]
                   if not (ROOT / "assets" / "luts" / f"ios_{preset['id']}.cube").is_file()]
    assert not missing_ios


def test_ios_flow_has_import_edit_develop_gallery_and_share():
    content = (IOS / "Views" / "ContentView.swift").read_text(encoding="utf-8")
    editor = (IOS / "Views" / "FrameEditorView.swift").read_text(encoding="utf-8")
    store = (IOS / "Services" / "RollStore.swift").read_text(encoding="utf-8")
    assert "fileImporter" in content and "developedOnly: true" in content
    assert "FILTER INTENSITY" in editor and "Develop JPEG" in editor
    assert "ShareLink" in editor and "beforePreview" in editor
    assert "saveDevelopedJPEG" in store and 'appendingPathComponent("Developed"' in store


def test_unsigned_ipa_workflow_builds_and_verifies_device_bundle():
    workflow = (ROOT / ".github" / "workflows" / "ios-unsigned-ipa.yml").read_text(
        encoding="utf-8"
    )
    assert "pull_request:" in workflow and "workflow_dispatch:" in workflow
    assert "-sdk iphoneos" in workflow
    assert "CODE_SIGNING_ALLOWED=NO" in workflow
    assert "com.lofilogic.ios" in workflow
    assert "LoFiLogic-iPhone-unsigned.ipa" in workflow
