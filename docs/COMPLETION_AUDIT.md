# P43 product completion audit

This audit records authoritative evidence for the Sony P43 Windows + iPhone/iPad redevelopment.

| Requirement | Evidence | Result |
| --- | --- | --- |
| Sony DSC-P43 is the reference input | `shared/presets.json`; EXIF detector and real-roll render | verified |
| Windows remains the primary editor | P43 folder button, batch UI, ACEScg/GPU engine, packaged EXE | verified |
| No connected-Flashback/DNG product flow | Windows toolbar/menu/export surface; Apple import surface | verified |
| 100% is a calibrated P43 endpoint | per-look LUT anchors plus residual optics/texture; 0–100% slider remains | verified |
| 0% is the source baseline | raster endpoint tests and Before render path | verified |
| P43 EXIF survives JPEG export | automated EXIF test and real `DSC00007.JPG` export | verified |
| Whole-folder and individual-file input | Windows file/folder flows and persistence tests | verified |
| Apple Photos and Files import | `PhotosPicker`, `fileImporter`, persistent Originals store, Xcode build | build-verified |
| Native iPhone and iPad target | `TARGETED_DEVICE_FAMILY: "1,2"`; inspected universal IPA metadata | build-verified |
| Save finished Apple render to Photos | add-only permission strings, `PHPhotoLibrary` export, Xcode build | build-verified |
| Free Windows-based sideload route | universal unsigned IPA workflow and AltStore guide | implemented |
| Updated Windows distributable | `dist_p43/LoFi Logic/LoFi Logic.exe`; packaged smoke | verified |

## Automated evidence

- 132 Python tests pass.
- Every Swift file parses without syntax errors using the Swift tree-sitter grammar.
- GitHub's macOS runner compiles and packages the universal arm64 app successfully.
- The packaged IPA contains device families 1 and 2, iOS 17.0 minimum metadata, the P43 catalog,
  all 20 presets, and the 100% default intensity.
- All 20 public preset LUTs and all 20 iOS companion LUTs are present.
- Real P43 roll auditing verifies all 20 full-strength looks without highlight or shadow crush.
- Its exported EXIF retains `SONY`, `DSC-P43`, and the original capture timestamp.
- The redesigned source window launches offscreen and exposes P43 folder/JPEG export controls.
- The new packaged Windows EXE stays running in an offscreen launch smoke test and contains the
  shared preset catalog.

## Physical-device verification still required

GitHub Actions has completed the Xcode compile and universal IPA packaging. Installation on a
physical iPhone and iPad through AltStore remains the final proof for Photos permission behavior,
real-device image rendering, and adaptive device layout.
