"""Generate auditable V1 camera-look LUTs from the calibration brief.

These are deliberately labelled provisional. They reuse the app's existing,
color-managed ACEScct->display LUTs as a base, resample to 33^3, then apply a
small documented display-space grade. Re-running this file reproduces every
generated cube exactly; later measured calibration can replace individual
specs without changing the renderer or preset IDs.
"""
from __future__ import annotations

from pathlib import Path
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.processor import LINSRGB_TO_ACESCG  # noqa: E402

LUTS = ROOT / "assets" / "luts"
SIZE = 33

# Per-look input anchors solved on the reference Sony DSC-P43 roll. Targets
# preserve source-average luminance for neutral families, with small explicit
# lifts for warm/faded families. This is calibration data, not UI intensity.
P43_INPUT_EVS = {
    "funsaver_800": 1.63, "quicksnap_400": 1.68,
    "rapid_retro_400": 1.64, "lomo_cn400": 1.67,
    "h35_gold_200": 1.66, "cs2_standard": 1.47,
    "cs2_vintage_1": 1.50, "cs2_vintage_2": 1.49,
    "cs2_vintage_3": 1.34, "cs2_analog": 1.15, "cs2_bw": 1.87,
    "paper_original": 1.39, "paper_bw": 1.85, "paper_blue": 1.43,
    "paper_sepia": 1.36, "don_retro": 1.41, "don_cool": 1.45,
    "don_warm": 1.44, "don_vivid": 1.48, "don_bw": 1.89,
}


SPECS = {
    # id: base, black lift, mid EV, saturation, RGB balance, contrast, mono,
    # warm-hue chroma reduction. The final field prevents a warm P43 capture
    # from being warmed a second time without cooling blues/greens.
    "funsaver_800":       ("disposable.cube", .015, .10, 1.00, (.985, 1.000, 1.010), 1.02, None, .78),
    "quicksnap_400":      ("disposable.cube", .010, .00, .96,  (.985, 1.000, 1.015), 1.01, None, .76),
    "rapid_retro_400":    ("disposable.cube", .025, -.05, 1.00, (.990, 1.000, 1.005), .98, None, .68),
    "lomo_cn400":         ("disposable.cube", .015, .05, 1.01, (.985, 1.000, 1.015), 1.01, None, .80),
    "h35_gold_200":       ("disposable.cube", .012, .08, 1.00, (.985, 1.000, 1.010), 1.02, None, .82),
    "cs2_standard":       ("pointandshoot.cube", .005, .05, 1.04, (1.010, 1.000, .995), 1.12, None, .30),
    "cs2_vintage_1":      ("pointandshoot.cube", .005, .05, 1.08, (1.010, 1.000, 1.005), 1.14, None, .32),
    "cs2_vintage_2":      ("pointandshoot.cube", .005, .00, 1.04, (.985, 1.000, 1.020), 1.12, None, .22),
    "cs2_vintage_3":      ("pointandshoot.cube", .015, .05, .90, (1.020, 1.005, .980), .92, None, .18),
    "cs2_analog":         ("pointandshoot.cube", .040, .10, .78, (1.010, .995, 1.005), .88, None, .05),
    "cs2_bw":             ("monochrome.cube", .025, .00, 0.0, (1.0, 1.0, 1.0), 1.10, (1.0, 1.0, 1.0), 0),
    "paper_original":     ("pointandshoot.cube", .020, -.05, .92, (1.010, 1.000, .990), 1.08, None, .25),
    "paper_bw":           ("monochrome.cube", .025, -.10, 0.0, (1.015, 1.0, .985), 1.06, (1.015, 1.0, .985), 0),
    "paper_blue":         ("pointandshoot.cube", .020, -.10, 1.02, (1.015, .990, 1.060), 1.06, None, .12),
    "paper_sepia":        ("pointandshoot.cube", .025, -.10, .90, (1.090, 1.025, .860), 1.04, None, .04),
    "don_retro":          ("pointandshoot.cube", .018, -.05, 1.03, (1.020, 1.000, .980), 1.09, None, .25),
    "don_cool":           ("pointandshoot.cube", .018, -.05, 1.03, (.950, 1.000, 1.065), 1.09, None, .12),
    "don_warm":           ("pointandshoot.cube", .018, -.05, 1.04, (1.060, 1.010, .950), 1.09, None, .08),
    "don_vivid":          ("pointandshoot.cube", .015, .00, 1.10, (1.005, 1.000, 1.005), 1.12, None, .28),
    "don_bw":             ("monochrome.cube", .030, -.05, 0.0, (1.005, 1.0, .995), 1.10, (1.005, 1.0, .995), 0),
}


def read_cube(path: Path):
    values, size = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        bits = line.strip().split()
        if not bits:
            continue
        if bits[0] == "LUT_3D_SIZE":
            size = int(bits[1])
        elif bits[0][0] in "-+.0123456789" and len(bits) >= 3:
            values.append(tuple(map(float, bits[:3])))
    table = np.asarray(values, dtype=np.float32).reshape(size, size, size, 3)
    return table


def resample(table: np.ndarray, size: int):
    source_n = table.shape[0]
    axis = np.linspace(0, source_n - 1, size, dtype=np.float32)
    out = np.empty((size, size, size, 3), dtype=np.float32)
    # All requested positions align for 65->33, but interpolation keeps this
    # generator correct if a later base LUT uses a different size.
    for r, rv in enumerate(axis):
        r0, r1, rf = int(np.floor(rv)), min(int(np.floor(rv)) + 1, source_n - 1), rv % 1
        for g, gv in enumerate(axis):
            g0, g1, gf = int(np.floor(gv)), min(int(np.floor(gv)) + 1, source_n - 1), gv % 1
            for b, bv in enumerate(axis):
                b0, b1, bf = int(np.floor(bv)), min(int(np.floor(bv)) + 1, source_n - 1), bv % 1
                c00 = table[r0, g0, b0] * (1-bf) + table[r0, g0, b1] * bf
                c01 = table[r0, g1, b0] * (1-bf) + table[r0, g1, b1] * bf
                c10 = table[r1, g0, b0] * (1-bf) + table[r1, g0, b1] * bf
                c11 = table[r1, g1, b0] * (1-bf) + table[r1, g1, b1] * bf
                c0 = c00 * (1-gf) + c01 * gf
                c1 = c10 * (1-gf) + c11 * gf
                out[r, g, b] = c0 * (1-rf) + c1 * rf
    return out


def neutralize_base_axis(table: np.ndarray):
    """Remove unintended color cast from the inherited LUT's neutral axis.

    The legacy Point & Shoot LUT pushes dark neutral inputs strongly toward
    red.  Correcting the base before the creative grade lets each preset add
    only its documented balance instead of inheriting that camera-specific WB.
    """
    n = table.shape[0]
    idx = np.arange(n)
    neutral = table[idx, idx, idx].astype(np.float32)
    luma = neutral @ np.array([.2126, .7152, .0722], dtype=np.float32)
    gains = luma[:, None] / np.maximum(neutral, .01)
    gains = np.clip(gains, .75, 1.35)
    r, g, b = np.meshgrid(idx, idx, idx, indexing="ij")
    position = ((r + g + b) / 3.0).reshape(-1)
    correction = np.stack([
        np.interp(position, idx, gains[:, channel]) for channel in range(3)
    ], axis=1).reshape(n, n, n, 3)
    return np.clip(table * correction, 0, 1).astype(np.float32)


def correct_warm_hue(x: np.ndarray, strength: float):
    """Keep warm subjects orange instead of letting the base LUT turn them red.

    The inherited disposable LUT suppresses green much more than blue in warm
    colors. Recovering green toward the red/blue midpoint rotates those colors
    back toward amber. A luminance compensation avoids changing exposure, and
    a small chroma compression keeps highly saturated reds from clipping.
    """
    if strength <= 0:
        return x
    luma = x[..., 0] * .2126 + x[..., 1] * .7152 + x[..., 2] * .0722
    dominance = np.clip((x[..., 0] - np.maximum(x[..., 1], x[..., 2])) / .18, 0, 1)
    mask = dominance * dominance * (3.0 - 2.0 * dominance)
    green_gap = np.maximum((x[..., 0] + x[..., 2]) * .5 - x[..., 1], 0)
    recovery = strength * mask * green_gap
    corrected = x.copy()
    corrected[..., 1] += recovery
    corrected -= (recovery * .7152)[..., None]
    corrected_luma = corrected[..., 0] * .2126 + corrected[..., 1] * .7152 + corrected[..., 2] * .0722
    chroma_scale = 1.0 - .50 * strength * mask
    return corrected_luma[..., None] + (corrected - corrected_luma[..., None]) * chroma_scale[..., None]


def grade(table, black, mid_ev, saturation, balance, contrast, mono, warm_control):
    x = np.clip(table, 0, 1).astype(np.float32)
    # Work in linear display light for exposure and channel balance.
    lin = np.where(x <= .04045, x / 12.92, ((x + .055) / 1.055) ** 2.4)
    lin *= (2.0 ** mid_ev) * np.asarray(balance, dtype=np.float32)
    x = np.where(lin <= .0031308, lin * 12.92, 1.055 * np.maximum(lin, 0) ** (1/2.4) - .055)
    luma = x[..., 0] * .2126 + x[..., 1] * .7152 + x[..., 2] * .0722
    if mono is not None:
        x = luma[..., None] * np.asarray(mono, dtype=np.float32)
    else:
        x = luma[..., None] + (x - luma[..., None]) * saturation
        x = correct_warm_hue(x, warm_control)
    x = .5 + (x - .5) * contrast
    x = black + (1.0 - black) * x
    return np.clip(x, 0, 1)


def write_cube(path: Path, table: np.ndarray, source: str):
    lines = [
        f'TITLE "LoFi Logic provisional {path.stem}"',
        f'# Generated by tools/generate_provisional_presets.py from {source}',
        '# Calibration status: PROVISIONAL — visual starting point, not a measured camera transform',
        f'LUT_3D_SIZE {table.shape[0]}', '',
    ]
    lines.extend(f"{r:.7f} {g:.7f} {b:.7f}" for r, g, b in table.reshape(-1, 3))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def sample_cube(table: np.ndarray, points: np.ndarray):
    n = table.shape[0]
    pos = np.clip(points, 0, 1) * (n - 1)
    lo = np.floor(pos).astype(np.int32)
    hi = np.minimum(lo + 1, n - 1)
    frac = pos - lo
    out = np.zeros_like(points, dtype=np.float32)
    for dr in (0, 1):
        ri = np.where(dr, hi[:, 0], lo[:, 0])
        rw = np.where(dr, frac[:, 0], 1-frac[:, 0])
        for dg in (0, 1):
            gi = np.where(dg, hi[:, 1], lo[:, 1])
            gw = np.where(dg, frac[:, 1], 1-frac[:, 1])
            for db in (0, 1):
                bi = np.where(db, hi[:, 2], lo[:, 2])
                bw = np.where(db, frac[:, 2], 1-frac[:, 2])
                out += table[ri, gi, bi] * (rw * gw * bw)[:, None]
    return out


def ios_input_lut(display_lut: np.ndarray, input_exposure_ev: float, size=33):
    """Compose P43 linear-sRGB -> calibrated ACEScct into an iOS LUT.

    The creative LUTs need a scene-to-LUT exposure anchor, but a finished P43
    JPEG must keep its already-rendered exposure. Windows applies the calibrated
    per-look value before ACEScct encoding; baking it here gives Core Image the
    identical full-strength endpoint.
    """
    axis = np.linspace(0, 1, size, dtype=np.float32)
    linear_srgb = np.array([(r, g, b) for r in axis for g in axis for b in axis],
                           dtype=np.float32)
    acescg = (linear_srgb @ LINSRGB_TO_ACESCG.T) * (2.0 ** input_exposure_ev)
    flat = np.maximum(acescg, 1e-10)
    encoded = np.where(
        flat <= .0078125,
        10.5402377416545 * flat + .0729055341958355,
        (np.log2(flat) + 9.72) / 17.52,
    ).astype(np.float32)
    return sample_cube(display_lut, encoded).reshape(size, size, size, 3)


def main():
    cache = {}
    for preset_id, spec in SPECS.items():
        source, black, mid_ev, sat, balance, contrast, mono, warm_control = spec
        if source not in cache:
            cache[source] = neutralize_base_axis(
                resample(read_cube(LUTS / source), SIZE))
        result = grade(cache[source], black, mid_ev, sat, balance, contrast, mono,
                       warm_control)
        write_cube(LUTS / f"{preset_id}.cube", result, source)
        write_cube(LUTS / f"ios_{preset_id}.cube",
                   ios_input_lut(result, P43_INPUT_EVS[preset_id]),
                   f"linear-sRGB + {preset_id}.cube")
        print(f"generated {preset_id}.cube")
    v1 = read_cube(LUTS / "V1.cube")
    write_cube(LUTS / "ios_flashback_classic_v1.cube", ios_input_lut(v1, 0.0),
               "linear-sRGB + V1.cube")
    print("generated ios_flashback_classic_v1.cube")


if __name__ == "__main__":
    main()
