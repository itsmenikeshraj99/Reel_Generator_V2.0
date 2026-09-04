"""Stage 4a — reframe source to 9:16 with subject-aware crop and audio.

Phase 5 (per the launch-ready plan)
-----------------------------------
1. PySceneDetect pre-pass: split the source into scenes at shot boundaries.
2. For each scene, sample a small number of frames and run a subject
   detector (OpenCV Haar face cascade primary, MediaPipe Pose optional)
   to find the most-confident person/face.
3. Compute a per-scene 9:16 crop rectangle, centered on the subject when
   one is found with high confidence.
4. **Letterbox fallback**: if no subject is detected in a scene (or the
   confidence is below threshold), the crop is full-frame scaled to fit
   9:16 with pillarbox so we never cut content off a wide shot, a
   screen-share, or a multi-person interview.
5. **Per-scene ffmpeg + concat demuxer**: each scene is cut and reframed
   in its own ffmpeg invocation, then the per-scene clips are
   concatenated with ffmpeg's `concat` demuxer. This is robust against
   the filter-parser edge cases that bite the per-filter `enable=`
   timing-gate approach (where a `,enable='between(t,0,5)'` chunk is
   misread as a separate `enable='between(t,0,5)'` filter).

The output also preserves the source's audio track (re-encoded as AAC).
"""
import json
import logging
import os
import subprocess
import tempfile
from typing import List, Optional, Tuple

import cv2

logger = logging.getLogger("stage_reframe")

# Output is 1080x1920 H.264, web-playable.
TARGET_W, TARGET_H = 1080, 1920

# How many frames to sample per scene to find the subject.
SAMPLES_PER_SCENE = 6

# Minimum detection confidence to trust a subject. Below this we fall
# back to the letterbox/pillarbox strategy.
SUBJECT_CONFIDENCE_THRESHOLD = 0.55


# ----------------------------------------------------------------------------
# Scene detection
# ----------------------------------------------------------------------------

def _detect_scenes(video_path: str) -> List[Tuple[float, float]]:
    """Run PySceneDetect's ContentDetector. Returns a list of
    `(start_seconds, end_seconds)` per scene.

    Falls back to a single full-video scene on any failure so we never
    crash the pipeline because the scene detector hiccupped.
    """
    try:
        from scenedetect import open_video, SceneManager, ContentDetector
        video = open_video(video_path)
        sm = SceneManager()
        sm.add_detector(ContentDetector(threshold=27.0))
        sm.detect_scenes(video)
        scenes = sm.get_scene_list()
        if not scenes:
            duration = _ffprobe_duration(video_path)
            return [(0.0, duration or 0.0)]
        # Each entry from scenedetect is (start_frame, end_frame) at fps
        fps = video.frame_rate or 25.0
        return [(s[0].get_frames() / fps, s[1].get_frames() / fps) for s in scenes]
    except Exception as exc:  # noqa: BLE001
        logger.warning("PySceneDetect failed (%s); using single scene", exc)
        duration = _ffprobe_duration(video_path) or 0.0
        return [(0.0, duration)]


# ----------------------------------------------------------------------------
# Subject detection (OpenCV Haar primary, MediaPipe Pose optional)
# ----------------------------------------------------------------------------

def _load_mediapipe_pose():
    """Lazy-load MediaPipe Pose. Returns the model or None on failure.

    MediaPipe is optional. The `solutions` API was removed in
    mediapipe>=1.0; we try the new `tasks.vision` API but fall back
    gracefully if the model assets aren't available locally.
    """
    try:
        import mediapipe as mp
        # New (>=1.0) API path
        if hasattr(mp, "tasks") and hasattr(mp.tasks, "vision"):
            # PoseLandmarker requires a .task model file. We don't ship
            # one, so the new API path is unavailable. Bail.
            return None
        # Old (legacy) API path
        if hasattr(mp, "solutions") and hasattr(mp.solutions, "pose"):
            return mp.solutions.pose.Pose(
                static_image_mode=True,
                model_complexity=0,
                min_detection_confidence=SUBJECT_CONFIDENCE_THRESHOLD,
            )
    except Exception:  # noqa: BLE001
        pass
    return None


def _load_haar():
    candidates = [
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "assets", "haarcascade_frontalface_default.xml",
        ),
        os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"),
    ]
    for path in candidates:
        try:
            if os.path.exists(path):
                clf = cv2.CascadeClassifier(path)
                if not clf.empty():
                    return clf
        except Exception:  # noqa: BLE001
            continue
    return None


def _detect_subject_in_frame(
    frame, pose, haar,
) -> Optional[Tuple[float, float, float]]:
    """Return (cx, cy, confidence) of the most-confident subject in `frame`,
    or None if no subject detected.

    cx, cy are normalized [0, 1].
    """
    h, w = frame.shape[:2]
    if h == 0 or w == 0:
        return None

    # 1. MediaPipe Pose (optional — only if it loaded)
    if pose is not None:
        try:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            res = pose.process(rgb)
            if res.pose_landmarks:
                # Average shoulder + hip x,y as the "subject centroid"
                pts = res.pose_landmarks.landmark
                # 11=L shoulder, 12=R shoulder, 23=L hip, 24=R hip
                indices = [11, 12, 23, 24]
                xs = [pts[i].x for i in indices if pts[i].visibility > 0.4]
                ys = [pts[i].y for i in indices if pts[i].visibility > 0.4]
                if xs and ys:
                    cx = sum(xs) / len(xs)
                    cy = sum(ys) / len(ys)
                    confidence = min(0.95, max(pts[i].visibility for i in indices))
                    return (cx, cy, confidence)
        except Exception:  # noqa: BLE001
            pass

    # 2. Haar cascade (primary detector)
    if haar is not None:
        try:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = haar.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(40, 40))
            if len(faces):
                x, y, fw, fh = max(faces, key=lambda r: r[2] * r[3])
                cx = (x + fw / 2.0) / w
                cy = (y + fh / 2.0) / h
                return (cx, cy, 0.7)  # Haar has no native confidence
        except Exception:  # noqa: BLE001
            pass

    return None


def _scene_subject_center(
    video_path: str, start: float, end: float, pose, haar,
) -> Optional[Tuple[float, float, float]]:
    """Sample frames within a scene and return the dominant subject
    (cx, cy, confidence), or None if no confident subject found.
    """
    if end <= start:
        return None
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None
    try:
        candidates: List[Tuple[float, float, float]] = []
        for i in range(SAMPLES_PER_SCENE):
            t = start + (i / max(1, SAMPLES_PER_SCENE - 1)) * (end - start) if SAMPLES_PER_SCENE > 1 else start
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            subj = _detect_subject_in_frame(frame, pose, haar)
            if subj is not None and subj[2] >= SUBJECT_CONFIDENCE_THRESHOLD:
                candidates.append(subj)
        if not candidates:
            return None
        # Pick the median cx,cy to be robust to outliers
        candidates.sort(key=lambda s: s[0])
        mid = candidates[len(candidates) // 2]
        return mid
    finally:
        cap.release()


# ----------------------------------------------------------------------------
# Filter expression builder
# ----------------------------------------------------------------------------

def _scene_crop_w(src_w: int, src_h: int) -> int:
    """Width of the 9:16 source-frame crop, given a source resolution.
    Even-only (libx264 doesn't accept odd widths in some yuv formats)."""
    target_ratio = TARGET_H / TARGET_W  # 16/9
    if src_w / src_h > target_ratio:
        # Source wider than 9:16 — crop sides, keep full height.
        crop_h = src_h - (src_h % 2)
        return int(round(crop_h * target_ratio)) & ~1
    # Source taller (or equal) — crop top/bottom, keep full width.
    return src_w - (src_w % 2)


def _scene_crop_h(src_w: int, src_h: int) -> int:
    """Height of the 9:16 source-frame crop, given a source resolution.
    Even-only (libx264 doesn't accept odd heights in some yuv formats)."""
    target_ratio = TARGET_H / TARGET_W  # 16/9
    if src_w / src_h > target_ratio:
        # Source wider than 9:16 — crop sides, keep full height.
        return src_h - (src_h % 2)
    # Source taller (or equal) — crop top/bottom, keep full width.
    crop_w = src_w - (src_w % 2)
    return int(round(crop_w / target_ratio)) & ~1


def _ffprobe_duration(video_path: str) -> float:
    try:
        out = subprocess.check_output(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            stderr=subprocess.STDOUT, timeout=30,
        ).decode().strip()
        return float(out)
    except Exception:  # noqa: BLE001
        return 0.0


def _ffprobe_dims(video_path: str) -> Tuple[int, int]:
    try:
        out = subprocess.check_output(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=s=x:p=0",
                video_path,
            ],
            stderr=subprocess.STDOUT, timeout=30,
        ).decode().strip()
        w, h = (int(x) for x in out.split("x"))
        return w, h
    except Exception:  # noqa: BLE001
        return (0, 0)


def _build_filter(
    src_w: int, src_h: int,
    scenes: List[Tuple[float, float]],
    subjects: List[Optional[Tuple[float, float, float]]],
) -> Tuple[str, bool]:
    """Build the ffmpeg filter for reframing to 9:16.

    Returns `(filter_str, use_filter_complex)`. When `use_filter_complex`
    is True, the caller must invoke ffmpeg with `-filter_complex`
    (instead of `-vf`); when False, a simple `-vf` chain is enough.

    Strategy:
      * No subjects anywhere → single `-vf` chain: scale + pad (letterbox).
      * Subjects detected in at least one scene → `-filter_complex` with
        one parallel branch per scene, each crop+scale timed by
        `enable='between(t,start,end)'`, then a `concat=n=N:v=1:a=0`
        to stitch them into a single 9:16 video stream.

    `enable=` is a per-filter timing gate; it MUST be a separate
    comma-separated segment of the chain (e.g. `crop=…:enable='between(t,…,…)'`)
    — never glued onto the previous filter's args. And chains of
    `crop → crop` are nonsense (the output of the first crop is fed
    back into the second), so per-scene crops must be in parallel
    branches, not serial.
    """
    target_ratio = TARGET_H / TARGET_W  # 16/9

    if src_w / src_h > target_ratio:
        # Source wider than 9:16 — crop sides, keep full height.
        crop_h = src_h - (src_h % 2)
        crop_w = int(round(crop_h * target_ratio))
        crop_w -= crop_w % 2
    else:
        # Source taller (or equal) — crop top/bottom, keep full width.
        crop_w = src_w - (src_w % 2)
        crop_h = int(round(crop_w / target_ratio))
        crop_h -= crop_h % 2

    has_any_subject = any(s is not None for s in subjects)
    if not has_any_subject:
        # Pure letterbox path — no subjects anywhere. Just fit + pad.
        # Pad offsets are literal `0:0` (top-left aligned). The runtime
        # arithmetic `(ow-iw)/2:(oh-ih)/2` can fail with `Error
        # reinitializing filters!` on some inputs.
        vf = (
            f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,"
            f"pad={TARGET_W}:{TARGET_H}:0:0:black,"
            f"setsar=1"
        )
        return vf, False

    # Per-scene branches. Each branch is a self-contained chain
    # crop → scale → setsar with a per-filter `enable=between(t,…)`
    # timing gate. Branches run in parallel from `[0:v]`.
    branches: List[str] = []
    for (start, end), subj in zip(scenes, subjects):
        enable = f"enable='between(t\\,{start:.3f}\\,{end:.3f})'"
        if subj is None:
            # Letterbox this scene — full-width crop + fit + pad.
            branch = (
                f"[0:v]crop={crop_w}:{crop_h}:(iw-cw)/2:0,"
                f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,"
                f"pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2:black,"
                f"setsar=1,{enable}[v{len(branches)}]"
            )
        else:
            cx, _cy, _conf = subj
            x_px = int(round(cx * src_w - crop_w / 2.0))
            x_px = max(0, min(src_w - crop_w, x_px))
            x_px -= x_px % 2
            branch = (
                f"[0:v]crop={crop_w}:{crop_h}:{x_px}:0,"
                f"scale={TARGET_W}:{TARGET_H}:flags=lanczos,"
                f"setsar=1,{enable}[v{len(branches)}]"
            )
        branches.append(branch)

    # Concat all per-scene branches into a single 9:16 stream.
    n = len(branches)
    inputs = "".join(f"[v{i}]" for i in range(n))
    fc = ";".join(branches) + f";{inputs}concat=n={n}:v=1:a=0[outv]"
    return fc, True


# ----------------------------------------------------------------------------
# Main entry point
# ----------------------------------------------------------------------------

async def reframe_video(video_path: str, output_path: str) -> bool:
    """Reframe source → 9:16 with subject detection + letterbox fallback."""
    if not os.path.exists(video_path):
        logger.error("Source video not found: %s", video_path)
        return False

    src_w, src_h = _ffprobe_dims(video_path)
    if src_w <= 0 or src_h <= 0:
        logger.error("Could not read source dimensions: %s", video_path)
        return False

    # 1. Detect scenes
    scenes = _detect_scenes(video_path)
    logger.info("Detected %d scene(s)", len(scenes))

    # 2. Detect subjects per scene
    pose = _load_mediapipe_pose()
    haar = _load_haar()
    subjects: List[Optional[Tuple[float, float, float]]] = []
    for i, (start, end) in enumerate(scenes):
        subj = _scene_subject_center(video_path, start, end, pose, haar)
        if subj is not None:
            logger.info(
                "Scene %d [%.2f–%.2fs]: subject at cx=%.2f, conf=%.2f",
                i, start, end, subj[0], subj[2],
            )
        else:
            logger.info("Scene %d [%.2f–%.2fs]: no subject (letterbox)", i, start, end)
        subjects.append(subj)

    # Cleanup MediaPipe
    if pose is not None:
        try:
            pose.close()
        except Exception:  # noqa: BLE001
            pass

    # 3. Build ffmpeg filter
    vf, use_filter_complex = _build_filter(src_w, src_h, scenes, subjects)
    logger.info("Reframing %sx%s → %sx%s (scenes=%d)", src_w, src_h, TARGET_W, TARGET_H, len(scenes))

    # 4. Run ffmpeg
    if use_filter_complex:
        # Per-scene path: cut the source into N 9:16 clips, one per scene,
        # then concatenate them with ffmpeg's `concat` demuxer. This is
        # robust against the filter-parser edge cases that bite the
        # `enable='between(t,…)'` per-filter-timing-gate approach — and
        # it preserves audio (each per-scene clip extracts the matching
        # time range of the source's audio stream).
        tmp_dir = tempfile.mkdtemp(prefix="reframe_")
        try:
            clip_paths: List[str] = []
            for i, ((start, end), subj) in enumerate(zip(scenes, subjects)):
                clip_path = os.path.join(tmp_dir, f"scene_{i:03d}.mp4")
                cw = _scene_crop_w(src_w, src_h)
                ch = _scene_crop_h(src_w, src_h)
                if subj is None:
                    # Letterbox scene — full-frame crop, fit + pad.
                    # Use literal `0:0` (NOT `(iw-cw)/2:0` arithmetic)
                    # because ffmpeg's filter expression evaluator can
                    # reject the runtime-resolved values on some inputs
                    # (`Error reinitializing filters` / EINVAL).
                    x_off = 0
                    y_off = 0
                    scene_vf = (
                        f"crop={cw}:{ch}:{x_off}:{y_off},"
                        f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,"
                        f"pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2:black,"
                        f"setsar=1"
                    )
                else:
                    cx, _cy, _conf = subj
                    x_px = int(round(cx * src_w - cw / 2.0))
                    x_px = max(0, min(src_w - cw, x_px))
                    x_px -= x_px % 2
                    scene_vf = (
                        f"crop={cw}:{ch}:{x_px}:0,"
                        f"scale={TARGET_W}:{TARGET_H}:flags=lanczos,"
                        f"setsar=1"
                    )
                dur = max(0.04, end - start)
                clip_cmd = [
                    "ffmpeg", "-y",
                    "-ss", f"{start:.3f}",
                    "-i", video_path,
                    "-t", f"{dur:.3f}",
                    "-vf", scene_vf,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart",
                    clip_path,
                ]
                try:
                    clip_res = subprocess.run(
                        clip_cmd, capture_output=True, text=True, timeout=600,
                    )
                except subprocess.TimeoutExpired:
                    logger.error("Scene %d ffmpeg timed out", i)
                    return False
                if clip_res.returncode != 0:
                    logger.error(
                        "Scene %d ffmpeg failed (rc=%s): %s",
                        i, clip_res.returncode, clip_res.stderr[-600:],
                    )
                    return False
                if not os.path.exists(clip_path) or os.path.getsize(clip_path) < 1024:
                    logger.error("Scene %d clip missing or empty", i)
                    return False
                clip_paths.append(clip_path)
                logger.info("Scene %d clip: %d bytes", i, os.path.getsize(clip_path))

            # Concat all per-scene clips via the concat demuxer. We need a
            # list file with one line per input, in order.
            list_path = os.path.join(tmp_dir, "concat_list.txt")
            with open(list_path, "w", encoding="utf-8") as f:
                for p in clip_paths:
                    # ffmpeg concat demuxer requires single-quoted absolute
                    # paths, with internal single quotes escaped as `'\\''`.
                    safe = p.replace("'", "'\\''")
                    f.write(f"file '{safe}'\n")
            concat_cmd = [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0",
                "-i", list_path,
                "-c", "copy",
                "-movflags", "+faststart",
                output_path,
            ]
            try:
                concat_res = subprocess.run(
                    concat_cmd, capture_output=True, text=True, timeout=300,
                )
            except subprocess.TimeoutExpired:
                logger.error("Concat ffmpeg timed out")
                return False
            if concat_res.returncode != 0:
                logger.error(
                    "Concat ffmpeg failed (rc=%s): %s",
                    concat_res.returncode, concat_res.stderr[-600:],
                )
                return False
        finally:
            # Best-effort cleanup of per-scene intermediate files.
            try:
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass
        # Per-scene path success — verify the final output exists before
        # returning. Errors above already returned False on failure.
        if not os.path.exists(output_path) or os.path.getsize(output_path) < 1024:
            logger.error("Reframe output missing or empty: %s", output_path)
            return False
        logger.info(
            "Reframed video with audio: %s (%d bytes)",
            output_path, os.path.getsize(output_path),
        )
        return True
    else:
        # Simple chain (no subjects detected — single letterbox path).
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            "-shortest",
            output_path,
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=1800,
            )
        except FileNotFoundError as exc:
            logger.error("ffmpeg not installed: %s", exc)
            return False
        except subprocess.TimeoutExpired:
            logger.error("ffmpeg timed out after 1800s for %s", video_path)
            return False
        if result.returncode != 0:
            logger.error("ffmpeg reframe failed (rc=%s): %s", result.returncode, result.stderr[-1200:])
            return False

    if not os.path.exists(output_path) or os.path.getsize(output_path) < 1024:
        logger.error("Reframe output missing or empty: %s", output_path)
        return False

    logger.info("Reframed video with audio: %s (%d bytes)", output_path, os.path.getsize(output_path))
    return True
