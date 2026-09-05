# LoFi Logic

A preset-first photo lab for the **Sony Cyber-shot DSC-P43** and other compact-camera images.

LoFi Logic keeps the P43's real early-digital rendering—the direct flash, CCD colour, sharpening,
limited highlight latitude, and 4 MP texture—then lets you add a camera-inspired look with one
control: **Filter Intensity**.

## Product shape

### Windows — primary editor

The Windows application is the reference implementation and the best place to edit a full camera
folder. It provides:

- Sony DSC-P43 recognition from EXIF metadata.
- Multi-photo and whole-folder loading without moving or deleting originals.
- Twenty camera- and film-inspired presets.
- Per-photo preset and intensity, copy/paste, batch selection, and press-to-compare.
- GPU-accelerated ACEScg processing with a CPU fallback.
- Metadata-preserving JPEG export and persistent open/export folders.
- Compatibility import for common finished images and camera RAW files.

P43 presets are calibrated so **100% is the intended endpoint**, not an over-strong recipe hidden
behind a lower default. The slider spans 0–100%; 0% is the untouched colour-managed source and
lower values are optional creative variations.

Run it from source by double-clicking `START_WINDOWS.bat` or launch:

```powershell
.\.venv\Scripts\python.exe main.py
```

Typical workflow:

```text
Sony P43 → copy JPEG folder to Windows → Open P43 folder
          → choose a preset per photo → compare → export JPEG batch
```

## iPhone + iPad companion

`ios/LoFiLogicIOS` is a native SwiftUI/Core Image application targeting both iPhone and iPad.
It shares the Windows preset catalog and supports:

- Apple Photos and Files/folder import.
- Per-photo preset selection and 0–100% intensity.
- Hold-to-compare against the original.
- Metadata-preserving JPEG rendering.
- Save to Apple Photos and standard sharing.
- A persistent on-device import and export library.

The unsigned universal IPA is built by `.github/workflows/ios-unsigned-ipa.yml`. It can be installed
from Windows using AltStore and a free Apple Account; free provisioning must be refreshed every
seven days. See [the installation guide](docs/INSTALL_IPHONE_FREE_WINDOWS.md).

## Presets

The shared catalog includes FunSaver 800, QuickSnap 400, Rapid Retro, Simple Use CN400,
H35 + Gold 200, Camp Snap 2 modes, Paper Camera modes, and experimental DonCamera 2 directions.
Names ending in “-inspired” describe creative targets rather than manufacturer-approved profiles.

The canonical catalog is `shared/presets.json`. Windows reads its recipes from `core/config.py`;
iPhone and iPad load the corresponding `ios_*.cube` assets.

## Development and tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The Windows test suite also performs source/resource validation for the Apple project. A physical
Apple-device build is produced on GitHub's macOS runner.

- [Product structure](docs/PRODUCT_V2.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Preset calibration](docs/PRESET_CALIBRATION.md)
- [Free iPhone/iPad installation from Windows](docs/INSTALL_IPHONE_FREE_WINDOWS.md)

## License

[GPL-3.0](LICENSE). Camera and film product names belong to their respective owners.
