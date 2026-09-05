"""
Application-wide constants, dataclasses, and runtime configuration.

Two dataclasses model the two layers of user-mutable state:

  VibeConfig         — the "film stock" layer. Effect parameters that
                       define a vibe (halation, grain, LUT, etc.).
                       One instance per active vibe. Persisted via
                       core.vibe_state. Edited only in the debug panel.

  ImageAdjustments   — the per-image layer. Exposure, WB, tint,
                       push/pull, rotation, plus the id of the vibe
                       this image was last edited under. Travels with
                       the image; gets saved in a project.

Everything that used to be DebugConfig.X is now a field on VibeConfig.
"""
from dataclasses import dataclass, asdict, fields, replace
import math as _math
import os as _os

# =============================================================================
# RAW PIPELINE CONSTANTS
# =============================================================================

SENSOR_BLACK = 64

# Native ONE35 V2 sensor geometry. The DNG exporter writes the raw strip
# verbatim, so these must match the source file's ImageWidth/ImageLength.
# SENSOR_RAW_STRIP_BYTES is the fallback strip length used when StripByteCounts
# is missing from the source EXIF (10-bit packed: w*h*10/8).
SENSOR_WIDTH = 4144
SENSOR_HEIGHT = 3088
SENSOR_RAW_STRIP_BYTES = 15995840

# Slider zero for the WB knob. Matches the Flashback ForwardMatrix1's
# calibration illuminant (D55). The generic-raw path also targets this
# Kelvin so both paths land at the same neutral point.
BASE_KELVIN = 5500.0

# CIE D65 — the reference illuminant for libraw's daylight_whitebalance.
GENERIC_DAYLIGHT_K = 6504.0

# Fallback Bayer WB for cameras whose raw file lacks daylight_whitebalance.
GENERIC_DAYLIGHT_WB_FALLBACK = [2.0, 1.0, 1.6, 1.0]

# v2 profile tone curve, used by the DNG exporter (tag 50940) AND by the
# fallback render path when no LUT is active. Pairs of (input, output).
PROFILE_TONE_CURVE = [
    0.0, 0.0, 0.02, 0.02, 0.06, 0.10, 0.20, 0.42,
    0.40, 0.70, 0.78, 0.95, 1.0, 1.0,
]

# =============================================================================
# EXPOSURE PIPELINE TUNING
# =============================================================================

# v2 pipeline: constant render-time exposure lift (EV). Applied alongside
# user exposure_ev and NOT counteracted post-LUT, so it genuinely raises
# output brightness. Tune to compensate for the gap between the LUT's
# training input level and the clean camera-metered intermediate.
BASE_EXPOSURE_OFFSET_V2 = 2.0

# Generic (non-Flashback) raw path only: constant lift (EV) baked into the
# ACEScg intermediate at develop time, BEFORE the shared render pipeline runs.
# libraw's linear develop (no_auto_bright, gamma=1) normalizes the sensor's raw
# *white level* to 1.0, so mid-grey lands ~2 stops below where the FM1 Flashback
# develop puts it — the anchor that BASE_EXPOSURE_OFFSET_V2 was tuned against.
# This re-anchors the generic intermediate to that level so the per-make table /
# embedded BaselineExposure and the downstream base offset all behave correctly.
# Preset-independent (baked in develop, not render), so V1 vs other vibes keep
# the same relative exposure relationship for generic raws as for FM1 raws.
GENERIC_RAW_ANCHOR_EV = 2.0

# Static linear-space boost applied AFTER reverse-AE and BEFORE ACEScct encode.
# Must match the value used by tools/build_color_charts.py when sampling the
# digital chart, otherwise the LUT's input domain at runtime won't match what
# colormatch saw at training time.
POST_AE_EXPOSURE_BOOST_EV = 2.0

# Fraction of the full reverse-AE + boost effect applied at slider zero.
# 0.0 = camera-metered look (AE fully preserved), 1.0 = old behavior (full
# reverse-AE + boost visible through the LUT). ~0.3 gives a mild film character
# while keeping brightness close to the camera-metered original.
REVERSE_AE_STRENGTH = 0.3

# "Push / Pull" slider extent, in EV (each direction). Pulling
# left scales the pre-LUT exposure down by 2^pp and counteracts it post-LUT
# (brightness ~unchanged, film toe more pronounced); pushing right does the
# opposite. Also drives grain highlight-bias.
PUSH_PULL_RANGE_EV = 2.0

# =============================================================================
# EFFECT DEFAULTS
# =============================================================================

# User-facing effect defaults. Units are documented per-field on VibeConfig.
# Conversion to the internal scalars the effect functions expect happens in
# the conversion helpers below; storage and UI both use these user-facing
# numbers.
CA_PIXELS = 5.0            # edge pixels of blue offset at the long edge of the rendered frame
# Legacy CA params — UNUSED by the current spectral CA (gpu.ca_frame /
# effects.apply_chromatic_aberration), which is driven solely by ca_pixels. Kept
# so existing presets/saved projects/UI don't break; candidates for repurposing
# or removal in a future cleanup.
CA_STEPS = 4
CA_BLUE_BLUR = 0.3         # px
CA_ZOOM_BLUR_PCT = 100.0   # percent
HALATION_THRESHOLD_STOPS = 4.5   # EV above middle grey
HALATION_BLUR_RADIUS = 8.0 # px
HALATION_STRENGTH_PCT = 75.0
# Warmth controls the per-scale halo chroma. 100% = the physically-grounded
# red-orange of colour-negative back-reflection (the visible halation hue);
# this default reproduces the legacy look's average colour, now applied as a
# radial gradient (near-neutral core → red-orange outer halo). 0% collapses to
# a colourless glow; >100% pushes toward the saturated no-remjet / CineStill
# halo. It scales the green/blue falloff exponentially around the baseline, so
# the hue direction is fixed (always reddens outward) and only its depth moves.
HALATION_WARMTH_PCT = 120.0
SOFTNESS_SIGMA = 0.5       # px
# Edge (corner) softness — a radial defocus that grows toward the frame corners,
# emulating lens field curvature. Distinct from the global `softness` blur.
EDGE_SOFTNESS_STRENGTH_PCT = 60.0   # 0–100 → max sharp→blur blend at the corners
EDGE_SOFTNESS_SIGMA = 3.0           # px, blur radius of the soft copy
EDGE_SOFTNESS_START_PCT = 40.0      # 0–100 → radius (as % of corner) where softness begins
GRAIN_STRENGTH_PCT = 50.0
GRAIN_TILE_SCALE = 0.8     # <1.0 makes grain finer (tiles render denser); >1.0 makes it chunkier.
GRAIN_HIGHLIGHT_BIAS = 0.3 # 1.0 = grain biased to highlights, 0.0 = shadows, 0.5 = flat.
SHARPEN_STRENGTH_PCT = 50.0
SHARPEN_RADIUS = 1.0       # px
CNR_AMOUNT_PCT = 20.0      # sigma 8 at _CNR_SIGMA_MAX=20 (a touch under old "200%")
CNR_DESPIKE_PCT = 60.0        # chroma firefly/outlier clamp; 0 = off
CNR_DESPIKE_BIAS_PCT = 75.0  # 0 = symmetric, 100 = green (-a*) only
VIGNETTE_STRENGTH_PCT = 50.0
VIGNETTE_COLOR_PCT = 25.0
VIGNETTE_CURVE = 0.0       # -100…+100, higher = more feathered (softer)
BLOOM_STRENGTH_PCT = 30.0
BLOOM_THRESHOLD_STOPS = 3.0      # EV above middle grey

# Internal scalar maxima — the user-facing percent fields map 0–100 onto
# 0–MAX. Keeping these explicit makes the migration buckets trivial to
# write and makes the panel/pipeline agree on the same conversion.
_CNR_SIGMA_MAX = 20.0
# Despike clamp limits in Lab a*/b* units: gentle band at amount→0+, tight at
# amount→100. Smooth colour stays inside the band; only spikes get pulled back.
_CNR_DESPIKE_T_HI = 40.0
_CNR_DESPIKE_T_LO = 4.0
_VIGNETTE_COLOR_MAX = 0.2


# =============================================================================
# UNIT CONVERSIONS  (user-facing values  →  internal effect scalars)
# =============================================================================
# Each helper takes a value as stored on VibeConfig and returns what the
# effect function actually consumes. The pipeline calls these at the
# effect-function boundary in core/processor.py.

def ca_pixels_to_scale(pixels: float, long_edge: int) -> float:
    """Edge-pixel offset → CA radial scale factor, normalised by the LONG edge.

    CA samples are displaced radially by s * radius; ``s = pixels / (long_edge/2)``
    so the displacement at the long half-edge is exactly ``pixels``. Normalising
    by the long edge (max(W, H)) makes the fringe invariant to orientation and to
    post-shoot 90° rotation — rotation swaps W and H but not their max — so a
    portrait and a landscape framing of the same scene fringe identically, as a
    real lens does. For a landscape frame the long edge IS the width, so existing
    ca_pixels values are unchanged; only portrait/rotated frames are corrected.
    """
    if long_edge <= 0:
        return 0.0
    return float(pixels) / (float(long_edge) / 2.0)


def pct(value: float) -> float:
    """0–N percent → 0–N/100 (the generic [0,1] mapping)."""
    return float(value) / 100.0


def vignette_curve_to_power(curve: float) -> float:
    """Symmetric -100…+100 curve → cosine-falloff exponent.

    0 → 1.0 (neutral). Higher = softer / more feathered (exponent < 1
    keeps falloff high until near the corners). Lower = harder edge
    (exponent > 1 pulls darkening inward).
    """
    return float(2.0 ** (-float(curve) / 50.0))


def cnr_pct_to_sigma(amount_pct: float) -> float:
    return pct(amount_pct) * _CNR_SIGMA_MAX


def cnr_sigma_color(sigma: float) -> float:
    """Bilateral range sigma for chroma NR, scaled with the spatial sigma.

    A fixed range sigma capped the strength: the bilateral preserved chroma
    variation near edges, so cranking the spatial sigma plateaued (it never
    removed the last ~12% of chroma noise). Scaling the range tolerance with the
    spatial sigma lets high settings approach a near-Gaussian chroma blur (much
    stronger), while a floor of 15 keeps low settings edge-preserving (no colour
    bleed across saturated boundaries). Single source of truth for both the
    numpy/cv2 path and gpu.cnr_frame.
    """
    return max(15.0, float(sigma) * 3.0)


def cnr_despike_thresholds(amount_pct: float, bias_pct: float) -> tuple:
    """Per-direction Lab clamp limits for the chroma despike prepass.

    Returns ``(thr_green, thr_other)`` in a*/b* units. The prepass clamps each
    chroma channel into ``[median +/- thr]`` of its 3x3 neighbourhood, killing
    isolated colour spikes (fireflies) while leaving smooth colour untouched (a
    smooth region equals its own median, so the deviation is ~0). A bilateral
    filter is edge-preserving and treats a one-pixel spike as an edge to keep,
    which is why cranking cnr_amount washes out detail before it removes the
    spike — this is the tool that actually removes it.

    The green direction (a* below the median) uses ``thr_green``; magenta (a*
    above) and both b* directions use ``thr_other``. ``bias`` widens
    ``thr_other`` so 100% acts on green only while 0% is symmetric. ``amount<=0``
    returns ``(0, 0)`` = off. Single source of truth for the cv2 path and
    gpu.cnr_frame.
    """
    amt = pct(amount_pct)
    if amt <= 0.0:
        return (0.0, 0.0)
    thr_green = _CNR_DESPIKE_T_HI - amt * (_CNR_DESPIKE_T_HI - _CNR_DESPIKE_T_LO)
    bias = min(0.999, max(0.0, pct(bias_pct)))
    thr_other = thr_green / (1.0 - bias)
    return (thr_green, thr_other)


def vignette_color_pct_to_shift(color_pct: float) -> float:
    return pct(color_pct) * _VIGNETTE_COLOR_MAX


# 18% middle grey, the reference point for the threshold-in-stops scale.
_MID_GREY_LINEAR = 0.18


def stops_above_mid_grey_to_acescct(stops: float) -> float:
    """Stops above 18% middle grey → ACEScct-encoded threshold.

    The bloom/halation passes mask on ACEScct-encoded luminance, which is
    why the prior 0–100% slider was opaque (ACEScct is a log encoding, so
    65% sat ~1.7 stops above scene white, not at "65% brightness"). This
    helper takes a photographer-friendly EV value and produces the same
    ACEScct number the effect functions expect.

    Skips the toe branch of the ACEScct encoder: anything brighter than
    linear 0.0078 is in the log range, which covers all sensible stops
    values (the toe crosses linear at acescct ≈ 0.155, equivalent to
    roughly -4.5 stops below middle grey — well below any threshold the
    effects care about).
    """
    linear = _MID_GREY_LINEAR * (2.0 ** float(stops))
    return float((_math.log2(max(linear, 1e-10)) + 9.72) / 17.52)

# =============================================================================
# HALATION SCALE MODEL
# =============================================================================
#
# The halation glow is built from three concentric scales blurred at growing
# radii, summed, then screen-blended. This replaces the older two-pass glow and
# gives the soft, wide falloff of a no-remjet stock while keeping small
# highlights crisp. Each scale carries its own chroma so the halo reddens
# outward (physically: back-reflected light is red-dominant after two passes
# through the upper dye layers and the orange base mask).
#
# Per scale: (radius_mult, thresh_offset, weight, green_frac, blue_frac, kind)
#   radius_mult   blur radius as a multiple of halation_blur_radius
#   thresh_offset added to the ACEScct threshold (wider tiers target only the
#                 very brightest, as the legacy second pass did)
#   weight        contribution to the summed glow (core dominant, halo fainter)
#   green/blue_frac  chroma at warmth=100%: green & blue relative to red. The
#                 warmth exponent (pct/100) is applied as frac**exp, so 100% is
#                 the physical baseline, 0% → neutral (frac**0 = 1), >100% →
#                 deeper red. Core ~ the legacy average; outer scales redder.
#   kind          'disc' = circle-of-confusion (defined edge — the back-
#                 reflection is a defocused copy of the highlights); 'exp' =
#                 exponential falloff (the fainter diffuse scatter tail).
#
# The defined CineStill halo is the disc core; the exp tails are the soft bloom
# of scattered light layered faintly on top.
HALATION_SCALES = (
    (1.0, 0.0,  1.00, 0.45, 0.12, 'disc'),  # core — defined defocus disc, dominant
    (2.5, 0.10, 0.18, 0.28, 0.05, 'exp'),   # near scatter — faint, brighter sources
    (5.0, 0.20, 0.07, 0.16, 0.02, 'exp'),   # far scatter — very faint pedestal
)


def halation_scale_tint(green_frac: float, blue_frac: float, weight: float,
                        warmth_pct: float):
    """Per-scale RGB tint (weight folded in) for the given warmth.

    Single source of truth for both the numpy oracle and gpu.halation_frame:
    red is the carrier (1.0); green/blue fall off as frac**(warmth_pct/100),
    so warmth 100% = physical baseline, 0% = colourless, >100% = redder.
    """
    exp = max(warmth_pct, 0.0) / 100.0
    return (weight, weight * (green_frac ** exp), weight * (blue_frac ** exp))


# =============================================================================
# DEBUG / TIMING
# =============================================================================

# Per-effect timing prints. Off by default; opt in via the LOFILOGIC_DEBUG_TIMING
# env var ("1" / "true" / "yes") so user installs stay quiet.
DEBUG_TIMING = _os.environ.get('LOFILOGIC_DEBUG_TIMING', '').lower() in ('1', 'true', 'yes')


def _timing_print(msg):
    """Print timing/debug messages. Controlled by DEBUG_TIMING flag."""
    if DEBUG_TIMING:
        print(msg)


# =============================================================================
# VIBE CONFIG (the "film stock" layer)
# =============================================================================

@dataclass
class VibeConfig:
    """All effect parameters that define a vibe.

    One instance per active vibe. Persisted via core.vibe_state.
    Constructed empty (all factory defaults) and then either tweaked by
    the user or seeded from a VIBE_PRESETS recipe via vibe_config_for().
    """
    # ---- effect toggles ----
    enable_halation: bool = True
    enable_chromatic_aberration: bool = True
    enable_softness: bool = True
    enable_edge_softness: bool = False
    enable_grain: bool = True
    enable_sharpen: bool = True
    enable_cnr: bool = True
    enable_lut: bool = True
    enable_vignette: bool = True
    enable_bloom: bool = True
    enable_digital_noise: bool = False
    enable_jpeg_artifacts: bool = False

    # ---- effect parameters (user-facing units; see conversion helpers) ----
    # Percent fields are stored as 0–N where N is each effect's natural max
    # (100 for clamped effects, 200/300/500 for ones that can over-drive).
    # Pixel fields are explicit pixel counts. Threshold fields are in EV
    # (stops) above 18% middle grey — 0 = middle grey, +N = N stops
    # brighter, default ≈ +4 (just into the specular highlight range).
    # vignette_curve is signed -100…+100 with 0 = neutral, positive = softer.
    halation_threshold_stops: float = HALATION_THRESHOLD_STOPS  # EV above mid grey
    halation_blur_radius: float = HALATION_BLUR_RADIUS         # px
    halation_strength_pct: float = HALATION_STRENGTH_PCT       # 0–300
    halation_warmth_pct: float = HALATION_WARMTH_PCT           # 0–300, 100 = physical
    ca_pixels: float = CA_PIXELS                                # edge px @ long edge
    ca_steps: int = CA_STEPS
    ca_blue_blur: float = CA_BLUE_BLUR                          # px
    ca_zoom_blur_pct: float = CA_ZOOM_BLUR_PCT                  # 0–500
    softness_sigma: float = SOFTNESS_SIGMA                      # px
    edge_softness_strength_pct: float = EDGE_SOFTNESS_STRENGTH_PCT  # 0–100
    edge_softness_sigma: float = EDGE_SOFTNESS_SIGMA            # px
    edge_softness_start_pct: float = EDGE_SOFTNESS_START_PCT    # 0–100 (% of corner radius)
    grain_strength_pct: float = GRAIN_STRENGTH_PCT              # 0–200
    sharpen_strength_pct: float = SHARPEN_STRENGTH_PCT          # 0–500
    sharpen_radius: float = SHARPEN_RADIUS                      # px
    cnr_amount_pct: float = CNR_AMOUNT_PCT                      # 0–100
    cnr_despike_pct: float = CNR_DESPIKE_PCT                    # 0–100 (chroma firefly clamp)
    cnr_despike_bias_pct: float = CNR_DESPIKE_BIAS_PCT          # 0 sym … 100 green-only
    vignette_strength_pct: float = VIGNETTE_STRENGTH_PCT        # 0–100
    vignette_color_pct: float = VIGNETTE_COLOR_PCT              # 0–100
    # Optional absolute RGB edge tint. 1/1/1 is neutral and preserves the
    # legacy cool-shift control above. Values below 1 absorb that channel at
    # the edge; this is needed for brown/amber plastic-lens corner casts.
    vignette_tint_r: float = 1.0
    vignette_tint_g: float = 1.0
    vignette_tint_b: float = 1.0
    vignette_curve: float = VIGNETTE_CURVE                      # -100…+100
    bloom_strength_pct: float = BLOOM_STRENGTH_PCT              # 0–100
    bloom_threshold_stops: float = BLOOM_THRESHOLD_STOPS        # EV above mid grey

    # Digital-camera texture. Unlike film grain this is generated as separate
    # luma/chroma sensor noise and can be followed by a final JPEG simulation.
    luma_noise_strength_pct: float = 0.0
    luma_noise_scale: float = 1.0
    chroma_noise_strength_pct: float = 0.0
    chroma_noise_scale: float = 2.0
    chroma_noise_correlation: float = 0.35
    shadow_noise_bias_pct: float = 50.0
    jpeg_artifact_strength_pct: float = 0.0
    jpeg_block_size: int = 8
    jpeg_chroma_degradation_pct: float = 0.0
    jpeg_ringing_strength_pct: float = 0.0
    jpeg_degrade_raster_inputs: bool = False
    grain_scale: float = 1.0

    # ---- reverse-AE (advanced) ----
    enable_reverse_autoexposure: bool = False
    reverse_autoexposure_t_ref: float = 1e-3
    enable_post_ae_exposure_boost: bool = False
    post_ae_exposure_boost_ev: float = POST_AE_EXPOSURE_BOOST_EV
    reverse_ae_strength: float = REVERSE_AE_STRENGTH

    # ---- pipeline tuning ----
    base_exposure_offset_v2: float = BASE_EXPOSURE_OFFSET_V2
    # Finished P43 JPEGs are display-referred, but the creative LUTs were
    # authored against the same +2 EV ACEScct input anchor as the original RAW
    # pipeline.  Keep that LUT-input calibration separate from the neutral
    # render so 0% remains the untouched JPEG and 100% reaches the authored
    # preset instead of feeding the LUT data two stops too dark.
    raster_lut_input_offset_ev: float = BASE_EXPOSURE_OFFSET_V2

    # ---- LUT + DNG metadata ----
    # Tagged LUT reference. One of:
    #   ""                     — no LUT (tone-curve fallback path)
    #   "factory:<id>"         — looked up in FACTORY_LUTS against the
    #                            current build's asset dir
    #   "user:<absolute path>" — user-imported .cube file on disk
    # Factory ids decouple a saved vibe from the install-time on-disk
    # location of bundled LUTs, so a moved/upgraded install can't load the
    # wrong file. The migrator rewrites legacy `lut_path` strings into
    # this tagged form.
    lut_ref: str = ''
    dng_profile_name: str = 'Flashback Standard'

    # Pre-1.5 custom LUT path, preserved by the migrator. Purely
    # informational — never read by the pipeline. Lets users find and
    # re-import their original .cube once they've regenerated it against
    # the v2 color pipeline. Cleared once the user re-imports a LUT.
    legacy_user_lut: str = ''

    # ---- serialization ----
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> 'VibeConfig':
        """Build a VibeConfig from a dict; unknown keys ignored, types coerced."""
        kwargs = {}
        known = {f.name: f.type for f in fields(cls)}
        for name, t in known.items():
            if name in d:
                try:
                    kwargs[name] = t(d[name]) if t is not bool else bool(d[name])
                except (TypeError, ValueError):
                    pass  # leave default
        return cls(**kwargs)

    def copy(self) -> 'VibeConfig':
        return replace(self)


# =============================================================================
# IMAGE ADJUSTMENTS (the per-image layer)
# =============================================================================

@dataclass
class ImageAdjustments:
    """Per-image choices for the preset-first editor.

    ``filter_intensity`` is the only user-facing adjustment.  The legacy
    exposure/WB/tint/push-pull fields remain readable so existing ``.lofi``
    projects do not become corrupt, but the streamlined UI no longer exposes
    them.  New edits leave those values neutral.
    """
    exposure_ev: float = 0.0
    wb_temp: float = 0.0
    tint: float = 0.0
    push_pull_ev: float = 0.0
    # Presets are calibrated for P43 input, so full strength is the intended
    # result. The slider remains a creative fade from the untouched JPEG.
    filter_intensity: float = 1.0
    rotation: int = 0
    active_vibe_id: str = ''   # filled in by the editor when an image loads

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> 'ImageAdjustments':
        kwargs = {}
        known = {f.name: f.type for f in fields(cls)}
        for name, t in known.items():
            if name in d:
                try:
                    kwargs[name] = t(d[name])
                except (TypeError, ValueError):
                    pass
        return cls(**kwargs)

    def copy(self) -> 'ImageAdjustments':
        return replace(self)


# =============================================================================
# VIBE PRESETS — recipes that seed a VibeConfig
# =============================================================================

# Preset values are in user-facing units (see the conversion helpers above).
# vignette_curve = -50 * log2(power), so the previous feather=0.4 / "softer"
# maps to curve ≈ +66.
# =============================================================================
# LUT REGISTRY — factory id → bundled file (relative to the install root,
# resolved through resource_path at load time so PyInstaller bundles and
# dev runs both work). Saved vibes store these ids, never raw paths, so a
# moved install never silently picks up the wrong file.
# =============================================================================

FACTORY_LUTS = {
    'disposable':           'assets/luts/disposable.cube',
    'flashback_classic_v1': 'assets/luts/V1.cube',
    'point_shoot':          'assets/luts/pointandshoot.cube',
    'rangefinder':          'assets/luts/rangefinder.cube',
    'monochrome':           'assets/luts/monochrome.cube',
    'funsaver_800':         'assets/luts/funsaver_800.cube',
    'quicksnap_400':        'assets/luts/quicksnap_400.cube',
    'rapid_retro_400':      'assets/luts/rapid_retro_400.cube',
    'lomo_cn400':           'assets/luts/lomo_cn400.cube',
    'h35_gold_200':         'assets/luts/h35_gold_200.cube',
    'cs2_standard':         'assets/luts/cs2_standard.cube',
    'cs2_vintage_1':        'assets/luts/cs2_vintage_1.cube',
    'cs2_vintage_2':        'assets/luts/cs2_vintage_2.cube',
    'cs2_vintage_3':        'assets/luts/cs2_vintage_3.cube',
    'cs2_analog':           'assets/luts/cs2_analog.cube',
    'cs2_bw':               'assets/luts/cs2_bw.cube',
    'paper_original':       'assets/luts/paper_original.cube',
    'paper_bw':             'assets/luts/paper_bw.cube',
    'paper_blue':           'assets/luts/paper_blue.cube',
    'paper_sepia':          'assets/luts/paper_sepia.cube',
    'don_retro':            'assets/luts/don_retro.cube',
    'don_cool':             'assets/luts/don_cool.cube',
    'don_warm':             'assets/luts/don_warm.cube',
    'don_vivid':            'assets/luts/don_vivid.cube',
    'don_bw':               'assets/luts/don_bw.cube',
}

# Tag prefixes used on VibeConfig.lut_ref. Keep these as the single source
# of truth — sites that build or parse refs must use the constants below.
LUT_REF_FACTORY = 'factory:'
LUT_REF_USER = 'user:'

def resolve_lut_ref(ref: str):
    """Resolve a tagged LUT reference to an absolute filesystem path.

    Returns (absolute_path, origin) where origin ∈ {'factory', 'user', None}.
    Returns (None, None) for an empty ref. Returns (None, origin) when the
    referenced LUT cannot be found — the caller decides whether to fall
    back to the vibe's factory LUT or surface a notice.
    """
    # Imported here (not at module top) to avoid a circular import:
    # core/__init__.py loads this module during package init.
    from . import resource_path
    if not ref:
        return None, None
    if ref.startswith(LUT_REF_FACTORY):
        fid = ref[len(LUT_REF_FACTORY):]
        rel = FACTORY_LUTS.get(fid)
        if not rel:
            return None, 'factory'
        abs_path = resource_path(rel)
        return (abs_path if _os.path.exists(abs_path) else None), 'factory'
    if ref.startswith(LUT_REF_USER):
        path = ref[len(LUT_REF_USER):]
        return (path if _os.path.exists(path) else None), 'user'
    # Unknown tag — treat as missing rather than guessing.
    return None, None


# `ca_pixels` is the blue fringe offset in pixels at the long half-edge of the
# rendered frame (see ca_pixels_to_scale — normalised by the long edge so it's
# orientation-invariant). The pipeline develops raws with half_size=True, so the
# rendered width is half the sensor width (2072 px for the ONE35 V2); 2–8 px is
# the visual baseline these presets are calibrated against.
#
# `ca_zoom_blur_pct` in the presets is legacy/inert — the spectral CA is driven
# only by ca_pixels (the radial spectral spread subsumes the old zoom-blur pass).
# Reusable profile layers. A preset is color x optics x texture, with optional
# overrides. Keeping these layers separate is what lets an H35 optical profile
# enlarge the selected film grain without pretending the camera is a film stock.
COLOR_PROFILES = {
    # Per-look P43 LUT anchors were solved against the reference roll. They
    # preserve source exposure (with small intentional family-specific deltas)
    # instead of using one global opacity or one global input exposure.
    'funsaver_800': {'lut_ref': 'factory:funsaver_800', 'raster_lut_input_offset_ev': 1.63},
    'quicksnap_400': {'lut_ref': 'factory:quicksnap_400', 'raster_lut_input_offset_ev': 1.68},
    'rapid_retro_400': {'lut_ref': 'factory:rapid_retro_400', 'raster_lut_input_offset_ev': 1.64},
    'lomo_cn400': {'lut_ref': 'factory:lomo_cn400', 'raster_lut_input_offset_ev': 1.67},
    'h35_gold_200': {'lut_ref': 'factory:h35_gold_200', 'raster_lut_input_offset_ev': 1.66},
    'cs2_standard': {'lut_ref': 'factory:cs2_standard', 'raster_lut_input_offset_ev': 1.47},
    'cs2_vintage_1': {'lut_ref': 'factory:cs2_vintage_1', 'raster_lut_input_offset_ev': 1.50},
    'cs2_vintage_2': {'lut_ref': 'factory:cs2_vintage_2', 'raster_lut_input_offset_ev': 1.49},
    'cs2_vintage_3': {'lut_ref': 'factory:cs2_vintage_3', 'raster_lut_input_offset_ev': 1.34},
    'cs2_analog': {'lut_ref': 'factory:cs2_analog', 'raster_lut_input_offset_ev': 1.15},
    'cs2_bw': {'lut_ref': 'factory:cs2_bw', 'raster_lut_input_offset_ev': 1.87},
    'paper_original': {'lut_ref': 'factory:paper_original', 'raster_lut_input_offset_ev': 1.39},
    'paper_bw': {'lut_ref': 'factory:paper_bw', 'raster_lut_input_offset_ev': 1.85},
    'paper_blue': {'lut_ref': 'factory:paper_blue', 'raster_lut_input_offset_ev': 1.43},
    'paper_sepia': {'lut_ref': 'factory:paper_sepia', 'raster_lut_input_offset_ev': 1.36},
    'don_retro': {'lut_ref': 'factory:don_retro', 'raster_lut_input_offset_ev': 1.41},
    'don_cool': {'lut_ref': 'factory:don_cool', 'raster_lut_input_offset_ev': 1.45},
    'don_warm': {'lut_ref': 'factory:don_warm', 'raster_lut_input_offset_ev': 1.44},
    'don_vivid': {'lut_ref': 'factory:don_vivid', 'raster_lut_input_offset_ev': 1.48},
    'don_bw': {'lut_ref': 'factory:don_bw', 'raster_lut_input_offset_ev': 1.89},
    # Backward-compatible profiles retained for existing projects.
    'legacy_disposable': {'lut_ref': 'factory:disposable'},
    'legacy_point_shoot': {'lut_ref': 'factory:point_shoot'},
    'legacy_rangefinder': {'lut_ref': 'factory:rangefinder'},
    'legacy_monochrome': {'lut_ref': 'factory:monochrome'},
    'flashback_v1': {'lut_ref': 'factory:flashback_classic_v1', 'base_exposure_offset_v2': 0.0},
}

CAMERA_OPTICAL_PROFILES = {
    # P43-native residual optics. The source JPEG already contains lens CA,
    # edge acuity and Sony sharpening, so these describe only what is still
    # needed to reach each target camera rather than recreating the whole lens.
    'funsaver': {'enable_chromatic_aberration': True, 'ca_pixels': 2.2,
        'softness_sigma': .25, 'enable_edge_softness': True,
        'edge_softness_strength_pct': 30.0, 'edge_softness_sigma': 1.8,
        'edge_softness_start_pct': 50.0, 'sharpen_strength_pct': 18.0,
        'sharpen_radius': .7, 'vignette_strength_pct': 10.0, 'vignette_curve': 35.0,
        'bloom_strength_pct': 3.0, 'halation_strength_pct': 1.5,
        'halation_threshold_stops': 5.0, 'halation_blur_radius': 4.0},
    'quicksnap': {'enable_chromatic_aberration': True, 'ca_pixels': 1.5,
        'softness_sigma': .20, 'enable_edge_softness': True,
        'edge_softness_strength_pct': 25.0, 'edge_softness_sigma': 1.5,
        'edge_softness_start_pct': 55.0, 'sharpen_strength_pct': 15.0,
        'sharpen_radius': .7, 'vignette_strength_pct': 8.0, 'vignette_curve': 40.0,
        'bloom_strength_pct': 2.0, 'halation_strength_pct': 1.0,
        'halation_threshold_stops': 5.0, 'halation_blur_radius': 4.0},
    'rapid_retro': {'enable_chromatic_aberration': True, 'ca_pixels': 2.5,
        'softness_sigma': .35, 'enable_edge_softness': True,
        'edge_softness_strength_pct': 40.0, 'edge_softness_sigma': 2.0,
        'edge_softness_start_pct': 45.0, 'sharpen_strength_pct': 10.0,
        'sharpen_radius': .8, 'vignette_strength_pct': 12.0, 'vignette_curve': 25.0,
        'bloom_strength_pct': 3.0, 'halation_strength_pct': 1.5,
        'halation_threshold_stops': 5.0, 'halation_blur_radius': 4.0},
    'lomo_simple_use': {'enable_chromatic_aberration': True, 'ca_pixels': 2.2,
        'softness_sigma': .30, 'enable_edge_softness': True,
        'edge_softness_strength_pct': 35.0, 'edge_softness_sigma': 1.8,
        'edge_softness_start_pct': 45.0, 'sharpen_strength_pct': 12.0,
        'sharpen_radius': .7, 'vignette_strength_pct': 10.0, 'vignette_curve': 35.0,
        'bloom_strength_pct': 3.0, 'halation_strength_pct': 1.5,
        'halation_threshold_stops': 5.0, 'halation_blur_radius': 4.0},
    'h35': {'enable_chromatic_aberration': True, 'ca_pixels': 2.5,
        'softness_sigma': .25, 'enable_edge_softness': True,
        'edge_softness_strength_pct': 45.0, 'edge_softness_sigma': 2.2,
        'edge_softness_start_pct': 40.0, 'sharpen_strength_pct': 10.0,
        'sharpen_radius': .7, 'vignette_strength_pct': 10.0, 'vignette_curve': 30.0,
        'grain_scale': 1.3},
    'camp_snap_2': {'enable_halation': False, 'enable_chromatic_aberration': True,
        'ca_pixels': .5, 'softness_sigma': .05, 'enable_edge_softness': True,
        'edge_softness_strength_pct': 15.0, 'edge_softness_sigma': 1.2,
        'edge_softness_start_pct': 55.0, 'sharpen_strength_pct': 20.0,
        'sharpen_radius': .6, 'vignette_strength_pct': 5.0, 'vignette_curve': 30.0,
        'bloom_strength_pct': 1.0},
    'paper_shoot_20mp': {'enable_halation': False, 'enable_chromatic_aberration': True,
        'ca_pixels': .5, 'softness_sigma': .15, 'enable_edge_softness': True,
        'edge_softness_strength_pct': 30.0, 'edge_softness_sigma': 1.8,
        'edge_softness_start_pct': 45.0, 'sharpen_strength_pct': 12.0,
        'sharpen_radius': .8, 'vignette_strength_pct': 12.0, 'vignette_curve': 25.0,
        'vignette_color_pct': 0.0, 'vignette_tint_r': 1.0,
        'vignette_tint_g': .92, 'vignette_tint_b': .84, 'bloom_strength_pct': 1.0},
    'doncamera_2': {'enable_halation': False, 'enable_chromatic_aberration': True,
        'ca_pixels': .75, 'softness_sigma': .15, 'enable_edge_softness': True,
        'edge_softness_strength_pct': 20.0, 'edge_softness_sigma': 1.5,
        'edge_softness_start_pct': 50.0, 'sharpen_strength_pct': 15.0,
        'sharpen_radius': .7, 'vignette_strength_pct': 7.0,
        'vignette_curve': 30.0, 'bloom_strength_pct': 2.0},
    'legacy_disposable': {'enable_chromatic_aberration': True, 'ca_pixels': 8.0,
        'softness_sigma': .5, 'sharpen_strength_pct': 200.0, 'sharpen_radius': .5,
        'vignette_strength_pct': 10.0, 'vignette_curve': 66.0, 'bloom_strength_pct': 15.0},
    'legacy_point_shoot': {'enable_chromatic_aberration': True, 'ca_pixels': 2.0,
        'softness_sigma': .3, 'sharpen_strength_pct': 50.0, 'sharpen_radius': 1.0,
        'vignette_strength_pct': 10.0, 'bloom_strength_pct': 10.0},
    'legacy_rangefinder': {'enable_chromatic_aberration': False, 'ca_pixels': 0.0,
        'softness_sigma': .1, 'sharpen_strength_pct': 80.0, 'sharpen_radius': 1.0,
        'vignette_strength_pct': 5.0, 'bloom_strength_pct': 5.0},
    'legacy_monochrome': {'enable_chromatic_aberration': False, 'ca_pixels': 0.0,
        'softness_sigma': .1, 'sharpen_strength_pct': 80.0, 'sharpen_radius': 1.0,
        'vignette_strength_pct': 20.0, 'bloom_strength_pct': 5.0},
    'flashback_v1': {'enable_chromatic_aberration': True, 'ca_pixels': 5.0,
        'softness_sigma': .3, 'sharpen_strength_pct': 80.0, 'sharpen_radius': .5,
        'vignette_strength_pct': 10.0, 'vignette_curve': 66.0, 'bloom_strength_pct': 3.0},
}

TEXTURE_PROFILES = {
    'film_800': {'enable_grain': True, 'enable_digital_noise': False, 'grain_strength_pct': 40.0},
    'film_400_fine': {'enable_grain': True, 'enable_digital_noise': False, 'grain_strength_pct': 28.0},
    'film_400_visible': {'enable_grain': True, 'enable_digital_noise': False, 'grain_strength_pct': 34.0},
    'film_200_half': {'enable_grain': True, 'enable_digital_noise': False, 'grain_strength_pct': 24.0},
    'camp_snap_2': {'enable_grain': False, 'enable_digital_noise': True,
        'luma_noise_strength_pct': .5, 'luma_noise_scale': 1.0,
        'chroma_noise_strength_pct': .2, 'chroma_noise_scale': 2.0,
        'chroma_noise_correlation': .3, 'shadow_noise_bias_pct': 65.0,
        'enable_jpeg_artifacts': True, 'jpeg_artifact_strength_pct': 4.0,
        'jpeg_chroma_degradation_pct': 6.0, 'jpeg_ringing_strength_pct': 4.0},
    'paper_shoot': {'enable_grain': False, 'enable_digital_noise': True,
        'luma_noise_strength_pct': .4, 'luma_noise_scale': 1.2,
        'chroma_noise_strength_pct': .2, 'chroma_noise_scale': 2.2,
        'chroma_noise_correlation': .35, 'shadow_noise_bias_pct': 70.0,
        'enable_jpeg_artifacts': True, 'jpeg_artifact_strength_pct': 6.0,
        'jpeg_chroma_degradation_pct': 10.0, 'jpeg_ringing_strength_pct': 3.0},
    'doncamera_2': {'enable_grain': False, 'enable_digital_noise': True,
        'luma_noise_strength_pct': .7, 'luma_noise_scale': 1.4,
        'chroma_noise_strength_pct': .35, 'chroma_noise_scale': 2.8,
        'chroma_noise_correlation': .4, 'shadow_noise_bias_pct': 80.0,
        'enable_jpeg_artifacts': True, 'jpeg_artifact_strength_pct': 10.0,
        'jpeg_chroma_degradation_pct': 16.0, 'jpeg_ringing_strength_pct': 8.0},
    'legacy_disposable': {'grain_strength_pct': 120.0},
    'legacy_point_shoot': {'grain_strength_pct': 80.0},
    'legacy_rangefinder': {'grain_strength_pct': 50.0},
    'legacy_monochrome': {'grain_strength_pct': 150.0},
    'flashback_v1': {'grain_strength_pct': 200.0},
}

PRESET_RECIPES = {
    'disposable': ('legacy_disposable', 'legacy_disposable', 'legacy_disposable'),
    'funsaver_800': ('funsaver_800', 'funsaver', 'film_800'),
    'quicksnap_400': ('quicksnap_400', 'quicksnap', 'film_400_fine'),
    'rapid_retro_400': ('rapid_retro_400', 'rapid_retro', 'film_400_visible'),
    'lomo_cn400': ('lomo_cn400', 'lomo_simple_use', 'film_400_fine'),
    'h35_gold_200': ('h35_gold_200', 'h35', 'film_200_half'),
    **{f'cs2_{mode}': (f'cs2_{mode}', 'camp_snap_2', 'camp_snap_2')
       for mode in ('standard', 'vintage_1', 'vintage_2', 'vintage_3', 'analog', 'bw')},
    **{f'paper_{mode}': (f'paper_{mode}', 'paper_shoot_20mp', 'paper_shoot')
       for mode in ('original', 'bw', 'blue', 'sepia')},
    **{f'don_{mode}': (f'don_{mode}', 'doncamera_2', 'doncamera_2')
       for mode in ('retro', 'cool', 'warm', 'vivid', 'bw')},
    'point_shoot': ('legacy_point_shoot', 'legacy_point_shoot', 'legacy_point_shoot'),
    'rangefinder': ('legacy_rangefinder', 'legacy_rangefinder', 'legacy_rangefinder'),
    'monochrome': ('legacy_monochrome', 'legacy_monochrome', 'legacy_monochrome'),
    'flashback_classic_v1': ('flashback_v1', 'flashback_v1', 'flashback_v1'),
}


def _compose_recipe(profile_ids):
    color_id, optical_id, texture_id = profile_ids
    merged = {}
    for layer, profile_id in ((COLOR_PROFILES, color_id),
                              (CAMERA_OPTICAL_PROFILES, optical_id),
                              (TEXTURE_PROFILES, texture_id)):
        merged.update(layer[profile_id])
    return merged


VIBE_PRESETS = {preset_id: _compose_recipe(ids)
                for preset_id, ids in PRESET_RECIPES.items()}

# Short, file-name-safe suffix per vibe — appended to exported JPGs as
# {basename}_{suffix}.jpg so users can tell at a glance which look produced
# which file. Unknown vibe ids fall back to 'edit'.
VIBE_EXPORT_SUFFIX = {
    'disposable':           'disp',
    'point_shoot':          'ps',
    'rangefinder':          'rf',
    'monochrome':           'mono',
    'flashback_classic_v1': 'v1',
    **{preset_id: preset_id.replace('_', '-') for preset_id in PRESET_RECIPES
       if preset_id not in {'disposable', 'point_shoot', 'rangefinder',
                            'monochrome', 'flashback_classic_v1'}},
}


def vibe_config_for(vibe_id: str) -> VibeConfig:
    """Construct a fresh VibeConfig from a preset recipe.

    All non-preset fields keep their factory defaults. The preset
    dictionary uses short keys (enable_ca, ca_pixels, softness, …);
    we map those onto the dataclass field names. All numeric preset
    values are in user-facing units (px, percent, signed curve).
    """
    cfg = VibeConfig()
    for field_name, value in VIBE_PRESETS[vibe_id].items():
        if hasattr(cfg, field_name):
            setattr(cfg, field_name, value)
    return cfg


# Names of every VibeConfig field — used by the debug panel to detect
# "modified from factory" state.
VIBE_FIELD_NAMES = tuple(f.name for f in fields(VibeConfig))
