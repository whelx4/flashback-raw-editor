# Preset calibration status

The camera-look catalog is built for finished Sony DSC-P43 JPEG input. Its presets are
reproducible starting points, not claims that a proprietary manufacturer color
pipeline has been reverse-engineered.

## Status labels

- `provisional`: enough independent or paired reference material exists for a
  V1 comparison pass; the bundled LUT still needs calibration against originals.
- `experimental`: the product and visual direction are known, but downloadable
  originals or controlled pairs are insufficient.
- `legacy`: retained from the original LoFi Logic renderer.

Product names in the UI use “-inspired” to make that distinction explicit.

## Implemented profile model

Every preset composes three reusable layers in `core/config.py`:

1. a color profile (ACEScct-to-display 3D LUT),
2. a camera optical profile (CA, softness, edge softness, vignette, bloom,
   halation and sharpening), and
3. a texture profile (film grain or digital luma/chroma noise and optional JPEG
   degradation).

The H35 implementation demonstrates the separation: an H35 optical profile is
combined with a provisional Gold 200-like color/texture profile, rather than
misrepresenting the camera body as a film emulsion.

Generated V1 LUTs are defined and reproduced by
`tools/generate_provisional_presets.py`. Each generated `.cube` embeds its
source LUT and calibration status in the header. Parameters are intentionally
coarse and auditable. The same generator emits `ios_<preset>.cube` companions
that compose linear-sRGB → ACEScg → ACEScct ahead of the creative LUT. This is
required because Core Image and the Windows renderer do not present the LUT
with the same input encoding; the iOS loader also explicitly converts `.cube`
B-fastest ordering to Core Image's R-fastest memory layout.

Each look has its own P43 LUT-input exposure anchor. Those anchors were solved
across the reference P43 roll against explicit family-specific luminance targets;
they are not a global intensity reduction. Windows applies the anchors before
ACEScct encoding and the generator bakes the same values into the iOS LUTs.
Optical sharpening, noise, grain, CA, softness and vignette are residual profiles:
they account for the rendering already present in the P43 JPEG.

The generated LUTs also neutralize unintended color on each inherited base
LUT's neutral axis. Warm subjects receive a luminance-preserving hue correction
before the final contrast stage: it restores the green component that the
legacy disposable LUT suppressed, preventing P43 skin, hair and wood from
collapsing toward magenta-red. On the indoor P43 calibration frame, the five
disposable-film looks keep red-versus-green separation within 14% of the clean
camera render while retaining their individual warm-film character.

## Reference manifest

| Family | Exact target | Status | Reference quality | Primary reference |
| --- | --- | --- | --- | --- |
| FunSaver | Current Kodak 27-exp, Kodak 800 speed | provisional | manufacturer specification plus same-lab comparisons | https://business.kodakmoments.com/product/kodak-fun-saver-single-use-camera |
| QuickSnap | Current Canada/global ISO-400, 32 mm f/10, 1/140 s | provisional | manufacturer specification plus same-lab comparisons | https://www.fujifilm.com/ca/en/consumer/films/quicksnap/specifications |
| Rapid Retro | Current product 2005154, ISO 400 | experimental | specifications and non-controlled samples | https://ilford.com/product/rapid-retro-camera/ |
| Simple Use CN400 | Full-frame SKU `suc100cn`, 31 mm f/9, 1/120 s | provisional | specification and downloadable samples | https://shop.lomography.com/simple-use-reloadable-film-camera-color-negative |
| H35 | Original H35, not H35N | experimental | optical specification; film rendering varies by stock/lab | https://www.kodak.retopro.co/products/kodak-ektar-h35-half-frame-film-camera |
| Camp Snap 2 | 2026 model, six modes | experimental | strong qualitative review, no stable matched originals | https://amateurphotographer.com/review/camp-snap-2-review/ |
| Paper Shoot | Current 20 MP board, four built-in modes | provisional | same-scene compressed web comparisons | https://www.papershoot.com/products/20-mp-camera-board |
| DonCamera | Digital Development Camera 2.0, five modes | experimental | promotional PNGs without original EXIF | https://doncamera.com/products/camara-revelado-digital-2 |

Paper Shoot relative-mode source files used for directional analysis:

- Original: https://amateurphotographer.com/wp-content/uploads/sites/7/2025/03/Colour-papershootPST00286.jpg?w=768
- B&W: https://amateurphotographer.com/wp-content/uploads/sites/7/2025/03/blackandwhite-papershootPST00288.jpg?w=768
- Blue: https://amateurphotographer.com/wp-content/uploads/sites/7/2025/03/Blues-papershootPST00290.jpg?w=768
- Sepia: https://amateurphotographer.com/wp-content/uploads/sites/7/2025/03/sepia-papershootPST00289.jpg?w=768

These files are compressed reviewer derivatives and are not redistributed with
the application. No reuse license was identified. They establish relative
direction only and are not treated as exact colorimetric targets.

The reproducible hashes, decoded dimensions, alignment inlier counts and robust
relative Lab measurements from the current retrieval are recorded in
[`preset_reference_analysis.json`](preset_reference_analysis.json). Re-run
`python tools/analyze_preset_references.py --output docs/preset_reference_analysis.json`
to audit whether any hosted derivative has changed.

## Reproducible calibration requirements

A preset can move from provisional/experimental to matched only after its local
calibration manifest records:

- original source and target file hashes;
- camera/product generation and mode;
- exposure and white-balance treatment;
- embedded ICC/profile handling;
- crop, registration, masks, rejected pixels and scene identifiers;
- analysis color space, transfer function, white point and code version; and
- validation error on held-out scenes, including skin, foliage, sky, flash and
  highlight-clipping examples.

Do not combine the current global QuickSnap references with the older regional
Superia X-TRA 400 / 1/100 s version.
