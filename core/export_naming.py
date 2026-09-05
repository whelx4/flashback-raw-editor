"""Aesthetic, deterministic export filenames.

Both the export loop and the "already processed?" check derive the output name
the same way: a pure function of the ONE35 V2 DNG path.

Naming:
  V2 DNGs   (``SN<serial>_<frame>``)  -> ``FBV2_<frame5>``  (e.g. FBV2_00042)
  other V2 filenames                  -> the original stem, unchanged
"""

import re
from pathlib import Path

# Camera-issued V2 filename shape: SN<serial>_<frame> (see editor's
# _CAMERA_DNG_PATTERN). We keep only the frame; the serial is noise.
_V2_FRAME_RE = re.compile(r'^SN\d+_(\d+)$', re.IGNORECASE)

def export_basename(file_path) -> str:
    """Return the suffix-less, extension-less export name for a source file."""
    p = Path(file_path)
    m = _V2_FRAME_RE.match(p.stem)
    if m:
        return f"FBV2_{int(m.group(1)):05d}"
    return p.stem
