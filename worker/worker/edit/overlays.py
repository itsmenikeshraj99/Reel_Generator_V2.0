"""On-screen text for reels: the hook title and the point labels.

Everything is drawn in the same subtitle (ASS) pass as the captions, so there is no extra
re-encode. Text is fitted by measuring real renders from the same subtitle engine ffmpeg uses
(so Devanagari shaping is accounted for), never by guessing character widths.

* Hook title: the first TITLE_SECONDS of the reel, at most two lines, in the space above the
  picture (or over the top of the picture, on a dark box, when there is no room above).
* Point labels: the first LABEL_SECONDS of every montage beat after the first, one line.
"""
import asyncio
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from worker.edit.canvas import CANVAS_H, CANVAS_W, Box

FONTS_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "assets", "fonts"))
LATIN_FONT = "Noto Sans"
DEVANAGARI_FONT = "Noto Sans Devanagari"

TITLE_SECONDS = 2.5
LABEL_SECONDS = 1.2
FADE_IN_MS, FADE_OUT_MS = 200, 300
TITLE_SIZES = (56, 48, 42, 36, 32)
LABEL_SIZES = (40, 36, 32, 28)
TITLE_MAX_LINES = 2
SAFE_TOP_FRAC = 0.12     # the top 12% is covered by app interfaces: no text there
TITLE_TOP_GAP = 10
TEXT_MARGIN_X = 40
MAX_TEXT_WIDTH = CANVAS_W - 2 * TEXT_MARGIN_X - 8   # 8 px of room for the outline
LABEL_GAP = 24           # between a label and the picture
IN_BAR_MIN_TOP = 397     # the picture must start this low for a title + label to fit above it
_DEFAULT_LABEL = re.compile(r"^S\d+-S\d+$")  # the snapper's placeholder when a beat had no label


def script_of(text: str) -> str:
    """'dev' if the text contains Devanagari, else 'lat' (picks the font)."""
    return "dev" if any("ऀ" <= ch <= "ॿ" for ch in text) else "lat"


def font_for(text: str) -> str:
    return DEVANAGARI_FONT if script_of(text) == "dev" else LATIN_FONT


@dataclass(frozen=True)
class OverlayLayout:
    boxed: bool          # True: text sits over the picture on a dark box (no room above it)
    title_margin_v: int  # distance of the title's top from the canvas top
    label_alignment: int  # 2 = just above the picture, 8 = same place as the title
    label_margin_v: int


def overlay_layout(box: Box, height: int = CANVAS_H) -> OverlayLayout:
    title_margin = int(height * SAFE_TOP_FRAC) + TITLE_TOP_GAP
    if box.top >= IN_BAR_MIN_TOP:
        return OverlayLayout(False, title_margin, 2, height - box.top + LABEL_GAP)
    return OverlayLayout(True, title_margin, 8, title_margin)


def _style(name: str, font: str, size: int, alignment: int, margin_v: int, boxed: bool) -> str:
    if boxed:  # libass draws the opaque box in the outline colour
        border, outline, shadow, outline_col = 3, 12, 0, "&H66000000"
    else:
        border, outline, shadow, outline_col = 1, 4, 2, "&H00000000"
    return (f"Style: {name},{font},{size},&H00FFFFFF,&H00FFFFFF,{outline_col},&H00000000,-1,0,0,0,100,100,0,0,"
            f"{border},{outline},{shadow},{alignment},{TEXT_MARGIN_X},{TEXT_MARGIN_X},{margin_v},1")


def style_lines(layout: OverlayLayout) -> str:
    """The four overlay styles (title/label x Latin/Devanagari) to add to the ASS header."""
    rows = []
    for kind, size, align, margin in (("Title", TITLE_SIZES[1], 8, layout.title_margin_v),
                                      ("Label", LABEL_SIZES[0], layout.label_alignment, layout.label_margin_v)):
        for tag, font in (("Lat", LATIN_FONT), ("Dev", DEVANAGARI_FONT)):
            rows.append(_style(kind + tag, font, size, align, margin, layout.boxed))
    return "\n".join(rows)


# ── What to show, and when ─────────────────────────────────────────────

@dataclass
class Item:
    kind: str       # "title" | "label"
    text: str
    start: float    # seconds on the reel's own timeline
    end: float
    size: int = 0   # filled in by fit_items
    lines: int = 1


def plan_items(title: str, shape: str, segments: Sequence[Dict[str, Any]],
               overlaps: Sequence[float] = ()) -> List[Item]:
    """The title for the first seconds, and a label at the start of each montage beat after the first.

    `overlaps[i]` is how many seconds the end of segment i overlaps the start of the next one (a
    crossfade), so later beats start that much earlier on the reel's timeline.
    """
    spans = []
    for i, seg in enumerate(segments):
        dur = float(seg["end_time"]) - float(seg["start_time"])
        if dur > 0:
            spans.append((dur, str(seg.get("title") or "").strip(), overlaps[i] if i < len(overlaps) else 0.0))
    total = sum(d for d, _, _ in spans) - sum(o for _, _, o in spans[:-1])
    items: List[Item] = []
    if title and total > 0:
        items.append(Item("title", title, 0.0, min(TITLE_SECONDS, total)))
    if shape == "montage":
        t = 0.0
        for index, (dur, label, overlap) in enumerate(spans):
            if index >= 1 and label and not _DEFAULT_LABEL.match(label):
                items.append(Item("label", label, t, min(t + LABEL_SECONDS, t + dur)))
            t += dur - overlap
    return items


def _escape(text: str) -> str:
    return re.sub(r"[{}\\]", "", text).replace("\n", " ").strip()


def _ass_time(seconds: float) -> str:
    cs = int(round(max(0.0, seconds) * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def ass_lines(items: Sequence[Item]) -> List[str]:
    """Dialogue lines for fitted items (drawn above the captions)."""
    out = []
    for it in items:
        style = ("Title" if it.kind == "title" else "Label") + ("Dev" if script_of(it.text) == "dev" else "Lat")
        text = r"\N".join(_escape(part) for part in it.text.split("\n"))
        out.append(f"Dialogue: 1,{_ass_time(it.start)},{_ass_time(it.end)},{style},,0,0,0,,"
                   + "{" + rf"\fs{it.size}\fad({FADE_IN_MS},{FADE_OUT_MS})" + "}" + text)
    return out


# ── Fitting text by measuring real renders ─────────────────────────────

def _ffmpeg_filter_path(path: str) -> str:
    return path.replace("\\", "/").replace(":", "\\:")


def measure_widths(strings: Sequence[str], font: str, size: int) -> List[int]:
    """Rendered pixel width of each string on one line (0 for an empty string).

    The strings are drawn together in one frame, each in its own horizontal band, so a single
    ffmpeg call measures a whole batch. A width of CANVAS_W means "at least this wide".
    """
    spacing = int(size * 1.7) + 6
    per_frame = max(1, (CANVAS_H - 20) // spacing)
    widths: List[int] = []
    for start in range(0, len(strings), per_frame):
        batch = strings[start:start + per_frame]
        header = ("[Script Info]\nScriptType: v4.00+\nWrapStyle: 2\nPlayResX: {w}\nPlayResY: {h}\n\n[V4+ Styles]\n"
                  "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
                  "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
                  "Alignment, MarginL, MarginR, MarginV, Encoding\n"
                  "Style: M,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,8,0,0,0,1\n\n"
                  "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
                  ).format(w=CANVAS_W, h=CANVAS_H, font=font, size=size)
        events = [f"Dialogue: 0,0:00:00.00,0:00:01.00,M,,0,0,{10 + i * spacing},,{_escape(s)}" for i, s in enumerate(batch)]
        fd, ass_path = tempfile.mkstemp(suffix=".ass", prefix="measure_")
        os.close(fd)
        try:
            with open(ass_path, "w", encoding="utf-8") as f:
                f.write(header + "\n".join(events) + "\n")
            vf = f"ass='{_ffmpeg_filter_path(ass_path)}':fontsdir='{_ffmpeg_filter_path(FONTS_DIR)}',format=gray"
            proc = subprocess.run(
                ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color=c=black:s={CANVAS_W}x{CANVAS_H}:d=1",
                 "-vf", vf, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                capture_output=True, timeout=60)
            if proc.returncode != 0 or len(proc.stdout) != CANVAS_W * CANVAS_H:
                raise RuntimeError(f"text measuring failed: {proc.stderr[-200:]!r}")
            frame = np.frombuffer(proc.stdout, dtype=np.uint8).reshape(CANVAS_H, CANVAS_W)
        finally:
            try:
                os.remove(ass_path)
            except OSError:
                pass
        for i, s in enumerate(batch):
            if not _escape(s):
                widths.append(0)
                continue
            band = frame[10 + i * spacing - 4: 10 + i * spacing + spacing - 4]
            cols = np.where((band > 96).any(axis=0))[0]
            widths.append(int(cols.max() - cols.min() + 1) if cols.size else 0)
    return widths


def fit_title(text: str, size_steps: Sequence[int] = TITLE_SIZES, max_lines: int = TITLE_MAX_LINES) -> Tuple[str, int, int]:
    """(text with a line break where needed, font size, number of lines) that fits MAX_TEXT_WIDTH
    in at most `max_lines` lines. Words are dropped from the end only if even the smallest size fails."""
    words = text.split()
    font = font_for(text)
    while words:
        for size in size_steps:
            candidates = [" ".join(words)]
            for k in range(1, len(words)):
                candidates += [" ".join(words[:k]), " ".join(words[k:])]
            widths = measure_widths(candidates, font, size)
            if widths[0] <= MAX_TEXT_WIDTH:
                return " ".join(words), size, 1
            if max_lines >= 2 and len(words) > 1:
                best: Optional[Tuple[int, int]] = None
                for k in range(1, len(words)):
                    worst = max(widths[2 * k - 1], widths[2 * k])
                    if worst <= MAX_TEXT_WIDTH and (best is None or worst < best[0]):
                        best = (worst, k)
                if best is not None:
                    k = best[1]
                    return " ".join(words[:k]) + "\n" + " ".join(words[k:]), size, 2
        words = words[:-1]
    return "", size_steps[-1], 0


def fit_label(text: str) -> Tuple[str, int]:
    """(text, font size) for a one-line label."""
    fitted, size, _ = fit_title(text, LABEL_SIZES, 1)
    return fitted, size


async def fit_items(items: Sequence[Item]) -> List[Item]:
    """Fit every item (measuring renders run off the event loop); items that cannot fit are dropped."""
    out: List[Item] = []
    for it in items:
        if it.kind == "title":
            text, size, lines = await asyncio.to_thread(fit_title, it.text)
        else:
            text, size = await asyncio.to_thread(fit_label, it.text)
            lines = 1 if text else 0
        if text and lines:
            out.append(Item(it.kind, text, it.start, it.end, size, lines))
    return out
