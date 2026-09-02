"""Input-format and camera-identification helpers.

LoFi Logic is centred on finished compact-camera JPEGs, with the Sony
Cyber-shot DSC-P43 as its reference input.  Generic raster and RAW formats
remain accepted by the Windows editor so an existing photo library does not
need to be converted before use.
"""

from functools import lru_cache
from pathlib import Path

from PIL import Image

RAW_EXTENSIONS = (
    '.dng', '.arw', '.nef', '.nrw', '.cr2', '.cr3', '.raf', '.orf',
    '.rw2', '.pef', '.srw', '.x3f', '.3fr', '.fff', '.iiq', '.rwl',
    '.raw',
)

RASTER_EXTENSIONS = (
    '.jpg', '.jpeg', '.png', '.tif', '.tiff', '.webp',
)

SUPPORTED_EXTENSIONS = RAW_EXTENSIONS + RASTER_EXTENSIONS


def is_raster_path(path) -> bool:
    return Path(path).suffix.lower() in RASTER_EXTENSIONS


def is_supported_path(path) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_EXTENSIONS


@lru_cache(maxsize=512)
def camera_make_model(path) -> tuple[str, str]:
    """Return normalised EXIF make/model strings for a finished image."""
    if not is_raster_path(path):
        return "", ""
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            make = str(exif.get(271, "")).strip()
            model = str(exif.get(272, "")).strip()
            return make, model
    except (OSError, ValueError):
        return "", ""


def is_sony_p43_path(path) -> bool:
    make, model = camera_make_model(str(path))
    return make.casefold() == "sony" and model.casefold() in {
        "dsc-p43", "sony dsc-p43"
    }
