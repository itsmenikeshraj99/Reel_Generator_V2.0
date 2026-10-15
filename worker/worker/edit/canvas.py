"""Place a video on a 9:16 portrait canvas without cropping or stretching.

The whole frame is scaled (keeping its exact display proportions) to the
largest size that fits the canvas, then centred; everything else is black.
A 16:9 landscape video fills the canvas width and sits in the vertical
middle with black above and below.
"""

CANVAS_W = 720
CANVAS_H = 1280


def fit_filter(width: int = CANVAS_W, height: int = CANVAS_H) -> str:
    """ffmpeg -vf chain: scale to fit (even sizes), square pixels, centre on black.

    Uses `dar` (display aspect ratio) so non-square-pixel sources keep their
    true proportions, and runs after ffmpeg's automatic rotation, so phone
    videos with rotation metadata come out upright.
    """
    ratio = f"{width}/{height}"
    return (
        f"scale=w='if(gt(dar,{ratio}),{width},trunc({height}*dar/2)*2)':"
        f"h='if(gt(dar,{ratio}),trunc({width}/dar/2)*2,{height})':flags=lanczos,"
        f"setsar=1,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"
    )


# ── Where the picture ends up, and where captions go ───────────────────

import json
import subprocess
from dataclasses import dataclass
from typing import Optional, Tuple

CAPTION_MIN_GAP_PX = 20    # least clear space kept between the picture and the caption text
CAPTION_GAP_PX = 60        # gap between the picture and the caption text
CAPTION_SAFE_BOTTOM = 0.85  # keep captions above the bottom 15% (covered by app interfaces)
CAPTION_TEXT_HEIGHT_PX = 90  # room for one caption line at the current font size
DEFAULT_CAPTION_ALIGNMENT = 2   # bottom-centre
DEFAULT_CAPTION_MARGIN_V = 420  # distance from the bottom edge


@dataclass(frozen=True)
class Box:
    left: int
    top: int
    width: int
    height: int

    @property
    def bottom(self) -> int:
        return self.top + self.height


@dataclass(frozen=True)
class CaptionLayout:
    alignment: int  # ASS alignment: 2 = bottom-centre, 8 = top-centre
    margin_v: int   # ASS vertical margin in canvas pixels
    in_bar: bool    # True when the captions sit in the black space under the picture


DEFAULT_CAPTION_LAYOUT = CaptionLayout(DEFAULT_CAPTION_ALIGNMENT, DEFAULT_CAPTION_MARGIN_V, False)


def picture_box(display_w: float, display_h: float, width: int = CANVAS_W, height: int = CANVAS_H) -> Box:
    """Where `fit_filter` puts a picture with this display shape (same maths, same rounding)."""
    dar = display_w / display_h
    if dar > width / height:
        w, h = width, int((width / dar) // 2 * 2)
    else:
        w, h = int((height * dar) // 2 * 2), height
    return Box((width - w) // 2, (height - h) // 2, w, h)


def probe_display_size(path: str) -> Optional[Tuple[float, float]]:
    """The video's true display size: pixel shape and phone rotation applied. None if unreadable."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-print_format", "json",
             "-show_entries", "stream=width,height,sample_aspect_ratio:stream_tags=rotate:stream_side_data=rotation",
             path],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout
        stream = json.loads(out)["streams"][0]
        w, h = float(stream["width"]), float(stream["height"])
        sar = stream.get("sample_aspect_ratio") or "1:1"
        try:
            num, den = sar.split(":")
            ratio = float(num) / float(den)
            if ratio > 0:
                w *= ratio
        except (ValueError, ZeroDivisionError):
            pass  # "N/A" or "0:1" means square pixels
        rotation = 0.0
        for side in stream.get("side_data_list", []):
            if "rotation" in side:
                rotation = float(side["rotation"])
        if not rotation:
            rotation = float(stream.get("tags", {}).get("rotate", 0) or 0)
        if round(abs(rotation)) % 180 == 90:
            w, h = h, w
        return (w, h) if w > 0 and h > 0 else None
    except Exception:  # noqa: BLE001
        return None


def caption_layout(box: Box, width: int = CANVAS_W, height: int = CANVAS_H) -> CaptionLayout:
    """Captions go in the black space below the picture when a line fits there inside the safe area;
    otherwise keep the default position (over the lower part of the picture)."""
    safe_bottom = int(height * CAPTION_SAFE_BOTTOM)
    if safe_bottom - box.bottom < CAPTION_TEXT_HEIGHT_PX + CAPTION_MIN_GAP_PX:
        return DEFAULT_CAPTION_LAYOUT
    return CaptionLayout(8, min(box.bottom + CAPTION_GAP_PX, safe_bottom - CAPTION_TEXT_HEIGHT_PX), True)
