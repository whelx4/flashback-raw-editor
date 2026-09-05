# Architecture

LoFi Logic is a Sony P43-first compact-camera photo lab with a Windows reference implementation
and a native iPhone/iPad companion.

## Shared contract

```text
P43 JPEG / finished image
          │
          ▼
ICC or sRGB decode → linear working image → selected preset/effects
          │                                  │
          └──────── 0% original ─────────────┘
                              linear-light blend (0–100%)
                                          │
                                          ▼
                           metadata-preserving JPEG export
```

`shared/presets.json` defines presentation order, names, LUT resources, categories, export suffixes,
and the 100% product default. Each full-strength endpoint is calibrated for P43 JPEG input; the
intensity control is an optional fade toward the untouched source.

## Windows

- `ui/editor.py` owns the main batch UI, persisted folders, per-photo state, and export flow.
- `core/input_formats.py` groups supported formats and identifies the Sony DSC-P43 from EXIF.
- `core/processor.py::ImageProcessor` decodes raster images with EXIF orientation and ICC handling,
  converts linear sRGB to ACEScg, runs the preset/effect pipeline, and exports JPEGs.
- `core/config.py` contains the Windows recipe parameters.
- `core/gpu.py`, `core/kernels.py`, and `core/shaders` provide the resident GPU pipeline with NumPy/
  OpenCV fallbacks.

The old `FlashbackProcessor` and `FlashbackEditor` symbols are aliases so saved projects and external
imports do not break. Legacy DNG helpers remain isolated source modules for compatibility and preset
research; they are not reachable from the P43 product interface.

Windows is the authoritative high-quality and batch-processing implementation.

## iPhone and iPad

- `Views/ContentView.swift` imports from Photos or Files and presents the three-tab product flow.
- `Services/RollStore.swift` persists originals, edits, source-camera metadata, and Photos export.
- `Services/ImageProcessor.swift` applies shared LUTs and mobile optical/texture equivalents with
  Core Image.
- `Views/FrameEditorView.swift` owns per-photo preset/intensity, Before comparison, export, and share.
- `project.yml` generates one universal iOS target for device families 1 (iPhone) and 2 (iPad).

The Apple app intentionally processes finished images rather than embedding Python, Qt, LibRaw, or
the Windows WGPU runtime. This keeps installation size and memory use appropriate for a 4 MP P43
workflow while preserving the shared creative catalog.

## Colour and intensity

Finished P43 JPEG values are decoded as sRGB unless an embedded ICC profile says otherwise. A neutral
render bypasses all preset effects. The selected preset is rendered separately and mixed with neutral
in linear light. Consequently 0% is a trustworthy Before image rather than a weak version of a LUT.

P43 files start at 100%. Each color profile owns a measured P43-to-LUT exposure anchor, while its
optical and texture profiles add only the residual character not already baked into the camera JPEG.
This avoids stacking Sony sharpening/noise and avoids using global opacity to conceal a bad endpoint.

## Metadata

Windows copies source EXIF, ICC and DPI data when writing a finished JPEG. iOS copies ImageIO source
properties. Both set Orientation to 1 because orientation and user rotation have already been baked
into pixels. Source camera/model and capture date therefore survive the normal edit/export workflow.
