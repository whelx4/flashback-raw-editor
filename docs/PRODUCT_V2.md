# Sony P43 product structure

## Product rules

- The Sony Cyber-shot DSC-P43 JPEG is the reference input.
- An imported photo keeps the camera's baked colour and texture as its 0% baseline.
- Every photo owns a preset and Filter Intensity value.
- New P43 imports start at 100%, the calibrated intended endpoint for each look.
- Originals are copied into the Apple app sandbox and never modified. The Windows app leaves
  originals in place.
- Removing a photo from an editing batch never deletes the source file.
- Finished output is a metadata-preserving JPEG.

## Windows — primary product

The PySide application is the reference editor. It accepts a whole P43 folder, detects the camera
from EXIF `Make=SONY` and `Model=DSC-P43`, converts the finished sRGB/ICC-managed JPEG into ACEScg,
applies a per-look P43 LUT-input anchor and residual optical/texture recipe, and blends the result
with the untouched baseline in linear light.

The visible editor contains a preset browser, one intensity slider, before/after comparison,
thumbnail batch selection, persistent input/output folders, and JPEG export. Generic raster and RAW
inputs remain compatibility features, but there is no connected-Flashback import or DNG export in
the product surface.

## iPhone and iPad

The SwiftUI application is universal (`TARGETED_DEVICE_FAMILY = 1,2`) and has three areas:

1. **Import** — choose images from Photos, Files, folders, or connected storage.
2. **Photo Lab** — open an import, choose a preset, adjust intensity, and hold for Before.
3. **Exports** — review finished images, save them to Photos, or share them.

Imports are copied into `Documents/Rolls/<id>/Originals`; exports live under
`Documents/Rolls/<id>/Developed`. The `Negatives` fallback in `RollStore` exists only to read data
created by older test builds.

Core Image performs the mobile render using the shared iOS LUTs and optical/texture equivalents.
JPEG encoding copies source ImageIO properties, normalises orientation to 1, and updates pixel
dimensions. Photo-library access uses add-only authorization.

## Transfer from the P43

On Windows, copy the `DCIM` images from the Memory Stick or connect the camera over USB, then choose
**Open P43 folder**. On iPhone/iPad, import through Apple Photos using a compatible camera adapter or
card reader, then select those images from LoFi Logic's Photos picker. Files import remains available
for cloud drives and folders copied from Windows.

## Distribution

Windows is run from source or packaged with the existing PyInstaller/Inno Setup configuration. The
Apple app is compiled as an unsigned universal IPA on GitHub Actions and signed locally with
AltStore. A free Apple Account works but the app must be refreshed within each seven-day window.
