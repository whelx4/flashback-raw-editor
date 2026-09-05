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
    # id: base, black lift, mid EV, saturation, RGB balance, contrast, mono
    "funsaver_800":       ("disposable.cube", .015, .10, 1.08, (1.025, 1.000, .970), 1.02, None),
    "quicksnap_400":      ("disposable.cube", .010, .00, .98,  (1.000, .980, 1.015), 1.01, None),
    "rapid_retro_400":    ("disposable.cube", .025, -.05, 1.04, (1.035, 1.000, .965), .98, None),
    "lomo_cn400":         ("disposable.cube", .015, .05, 1.10, (1.020, 1.000, 1.005), 1.01, None),
    "h35_gold_200":       ("disposable.cube", .012, .08, 1.07, (1.030, 1.000, .965), 1.02, None),
    "cs2_standard":       ("pointandshoot.cube", .005, .05, 1.10, (1.025, 1.000, .985), 1.12, None),
    "cs2_vintage_1":      ("pointandshoot.cube", .005, .05, 1.18, (1.020, 1.000, 1.015), 1.14, None),
    "cs2_vintage_2":      ("pointandshoot.cube", .005, .00, 1.08, (.975, 1.000, 1.035), 1.12, None),
    "cs2_vintage_3":      ("pointandshoot.cube", .015, .05, .90, (1.045, 1.010, .950), .92, None),
    "cs2_analog":         ("pointandshoot.cube", .040, .10, .78, (1.025, .985, 1.020), .88, None),
    "cs2_bw":             ("monochrome.cube", .025, .00, 0.0, (1.0, 1.0, 1.0), 1.10, (1.0, 1.0, 1.0)),
    "paper_original":     ("pointandshoot.cube", .020, -.05, .92, (1.020, 1.000, .980), 1.08, None),
    "paper_bw":           ("monochrome.cube", .025, -.10, 0.0, (1.03, 1.0, .96), 1.06, (1.03, 1.0, .96)),
    "paper_blue":         ("pointandshoot.cube", .020, -.10, 1.05, (1.040, .980, 1.100), 1.06, None),
    "paper_sepia":        ("pointandshoot.cube", .025, -.10, .92, (1.120, 1.035, .820), 1.04, None),
    "don_retro":          ("pointandshoot.cube", .018, -.05, 1.06, (1.035, 1.000, .965), 1.09, None),
    "don_cool":           ("pointandshoot.cube", .018, -.05, 1.06, (.930, .995, 1.090), 1.09, None),
    "don_warm":           ("pointandshoot.cube", .018, -.05, 1.08, (1.090, 1.015, .920), 1.09, None),
    "don_vivid":          ("pointandshoot.cube", .015, .00, 1.16, (1.015, 1.000, 1.005), 1.12, None),
    "don_bw":             ("monochrome.cube", .030, -.05, 0.0, (1.01, 1.0, .99), 1.10, (1.01, 1.0, .99)),
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


def grade(table, black, mid_ev, saturation, balance, contrast, mono):
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
        source, black, mid_ev, sat, balance, contrast, mono = spec
        if source not in cache:
            cache[source] = resample(read_cube(LUTS / source), SIZE)
        result = grade(cache[source], black, mid_ev, sat, balance, contrast, mono)
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
