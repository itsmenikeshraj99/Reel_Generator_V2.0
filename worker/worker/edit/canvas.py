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
