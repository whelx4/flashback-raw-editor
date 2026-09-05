"""Regression tests for the reproducible provisional LUT generator."""
import numpy as np

from tools.generate_provisional_presets import SPECS, correct_warm_hue


def _luma(rgb):
    return rgb @ np.array([.2126, .7152, .0722], dtype=np.float32)


def test_warm_hue_correction_preserves_luminance_and_restores_orange():
    # Representative red-biased output from the inherited disposable base LUT.
    source = np.array([[[.72, .34, .30]]], dtype=np.float32)
    corrected = correct_warm_hue(source, .8)

    assert np.allclose(_luma(corrected), _luma(source), atol=1e-6)
    assert corrected[0, 0, 0] - corrected[0, 0, 1] < source[0, 0, 0] - source[0, 0, 1]
    assert corrected[0, 0, 1] - corrected[0, 0, 2] > source[0, 0, 1] - source[0, 0, 2]


def test_disposable_family_has_p43_warm_hue_calibration():
    preset_ids = {
        "funsaver_800", "quicksnap_400", "rapid_retro_400",
        "lomo_cn400", "h35_gold_200",
    }
    for preset_id in preset_ids:
        warm_control = SPECS[preset_id][-1]
        assert .65 <= warm_control <= .85
