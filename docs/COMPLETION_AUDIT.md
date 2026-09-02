# P43 product completion audit

This audit records authoritative evidence for the Sony P43 Windows + iPhone/iPad redevelopment.

| Requirement | Evidence | Result |
| --- | --- | --- |
| Sony DSC-P43 is the reference input | `shared/presets.json`; EXIF detector and real-roll render | verified |
| Windows remains the primary editor | P43 folder button, batch UI, ACEScg/GPU engine, packaged EXE | verified |
| No connected-Flashback/DNG product flow | Windows toolbar/menu/export surface; Apple import surface | verified |
| Filters are gentler on finished P43 JPEGs | 60% persisted default on Windows and Apple; 0–100% slider remains | verified |
| 0% is the source baseline | raster endpoint tests and Before render path | verified |
| P43 EXIF survives JPEG export | automated EXIF test and real `DSC00007.JPG` export | verified |
| Whole-folder and individual-file input | Windows file/folder flows and persistence tests | verified |
| Apple Photos and Files import | `PhotosPicker`, `fileImporter`, and persistent Originals store | implemented; source-validated |
| Native iPhone and iPad target | `TARGETED_DEVICE_FAMILY: "1,2"` and universal IPA workflow | implemented; source-validated |
| Save finished Apple render to Photos | add-only permission strings and `PHPhotoLibrary` export | implemented; source-validated |
| Free Windows-based sideload route | universal unsigned IPA workflow and AltStore guide | implemented |
| Updated Windows distributable | `dist_p43/LoFi Logic/LoFi Logic.exe`; packaged smoke | verified |

## Automated evidence

- 132 Python tests pass.
- Every Swift file parses without syntax errors using the Swift tree-sitter grammar.
- All 20 public preset LUTs and all 20 iOS companion LUTs are present.
- A real Sony P43 JPEG renders at 2304×1728 with the 60% default.
- Its exported EXIF retains `SONY`, `DSC-P43`, and the original capture timestamp.
- The redesigned source window launches offscreen and exposes P43 folder/JPEG export controls.
- The new packaged Windows EXE stays running in an offscreen launch smoke test and contains the
  shared preset catalog.

## External Apple verification still required

Windows cannot run Xcode. GitHub Actions must compile the universal IPA, after which it must be
installed on a physical iPhone and iPad through AltStore. That external run is the remaining proof
for Swift type-checking, Photos permission behavior, and adaptive device layout.
