import numpy as np
from PIL import Image

from core.config import vibe_config_for
from core.input_formats import is_raster_path, is_supported_path, is_sony_p43_path
from core.processor import FlashbackProcessor, INPUT_RASTER, export_image


def test_supported_input_groups():
    for name in ("frame.dng", "frame.arw", "frame.nef", "frame.cr3", "frame.raf"):
        assert is_supported_path(name)
        assert not is_raster_path(name)
    for name in ("frame.jpg", "frame.jpeg", "frame.png", "frame.tiff", "frame.webp"):
        assert is_supported_path(name)
        assert is_raster_path(name)
    assert not is_supported_path("frame.txt")


def test_jpeg_develops_and_zero_intensity_is_near_original(tmp_path):
    source = np.zeros((48, 64, 3), dtype=np.uint8)
    source[..., 0] = np.arange(64, dtype=np.uint8)[None, :] * 3
    source[..., 1] = 96
    source[..., 2] = 160
    path = tmp_path / "input.jpg"
    Image.fromarray(source).save(path, quality=100, subsampling=0)

    processor = FlashbackProcessor(vibe=vibe_config_for("disposable"))
    assert processor.load_image(str(path)) is not None
    assert processor.input_kind == INPUT_RASTER
    assert not processor.is_flashback_file

    decoded = np.asarray(Image.open(path).convert("RGB"), dtype=np.float32) / 255.0
    neutral = processor.render_neutral_preview(downscale=False)
    assert neutral.shape == decoded.shape
    assert np.mean(np.abs(neutral - decoded)) < 0.01


def test_zero_intensity_ignores_preset_halation(tmp_path):
    source = np.zeros((48, 64, 3), dtype=np.uint8)
    source[20:28, 28:36] = 255
    path = tmp_path / "highlight.png"
    Image.fromarray(source).save(path)
    vibe = vibe_config_for("funsaver_800")
    vibe.halation_strength_pct = 300.0
    processor = FlashbackProcessor(vibe=vibe)
    processor.adjustments.filter_intensity = 0.0
    processor.load_image(str(path))
    neutral = processor.render_preview(downscale=False)
    decoded = np.asarray(Image.open(path).convert("RGB"), dtype=np.float32) / 255.0
    assert np.mean(np.abs(neutral - decoded)) < 0.01


def test_jpeg_simulation_is_not_stacked_on_raster_input(tmp_path):
    source = np.tile(np.arange(64, dtype=np.uint8)[None, :, None], (48, 1, 3)) * 3
    path = tmp_path / "already.jpg"
    Image.fromarray(source).save(path, quality=75)
    vibe = vibe_config_for("cs2_standard")
    processor = FlashbackProcessor(vibe=vibe)
    processor.load_image(str(path))
    assert vibe.enable_jpeg_artifacts
    assert not vibe.jpeg_degrade_raster_inputs


def test_p43_exif_is_detected_and_preserved_on_export(tmp_path):
    source = np.full((32, 48, 3), 120, dtype=np.uint8)
    path = tmp_path / "DSC00001.JPG"
    exif = Image.Exif()
    exif[271] = "SONY"
    exif[272] = "DSC-P43"
    exif[306] = "2026:08:29 14:53:13"
    Image.fromarray(source).save(path, quality=95, exif=exif)
    assert is_sony_p43_path(path)

    processor = FlashbackProcessor(vibe=vibe_config_for("funsaver_800"))
    assert processor.load_image(str(path)) is not None
    output = tmp_path / "DSC00001_fs800.jpg"
    assert export_image(processor, str(output))
    exported = Image.open(output).getexif()
    assert exported.get(271) == "SONY"
    assert exported.get(272) == "DSC-P43"
    assert exported.get(306) == "2026:08:29 14:53:13"
    assert exported.get(274, 1) == 1
