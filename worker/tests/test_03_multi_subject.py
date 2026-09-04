"""Test 03: Multi-subject video (face detection ambiguity).

The reframe stage should NOT crash when multiple subjects are present —
it should fall back to the median subject or to letterbox. We simulate
a 3-person frame and assert the detector doesn't blow up. We don't
assert WHICH subject is picked (that's environment-specific), only that
it returns SOMETHING reasonable.
"""
import asyncio
import os

import cv2
import numpy as np

from _helpers import test_case, assert_, section


async def main() -> bool:
    section("Test 03: Multi-subject video (3-person frame)")
    with test_case("reframe handles multiple subjects without crashing") as t:
        from worker.stages.reframe import _load_haar, _detect_subject_in_frame

        haar = _load_haar()
        assert_(haar is not None, "Haar cascade not loaded")

        # Build a 1920x1080 frame with 3 "face-like" regions at
        # different x positions. Haar won't detect the ellipses as faces
        # (it's trained on real face photos), but the test still verifies
        # the code path doesn't throw. If a real face detector is
        # available, the test will be more meaningful.
        frame = np.full((1080, 1920, 3), 220, dtype=np.uint8)
        for cx in (480, 960, 1440):
            cv2.ellipse(frame, (cx, 540), (100, 130), 0, 0, 360, (60, 60, 60), -1)
            cv2.circle(frame, (cx - 30, 510), 10, (240, 240, 240), -1)
            cv2.circle(frame, (cx + 30, 510), 10, (240, 240, 240), -1)
            cv2.ellipse(frame, (cx, 580), (35, 10), 0, 0, 180, (240, 240, 240), 2)

        # Run the detector. It may return None (no faces recognized in
        # synthetic data) — either way, the function must not throw.
        try:
            result = _detect_subject_in_frame(frame, None, haar)
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(f"detector raised: {exc}")

        # result is either None (no subject) or (cx, cy, conf). Both are valid.
        if result is not None:
            cx, cy, conf = result
            assert_(0.0 <= cx <= 1.0, f"cx out of range: {cx}")
            assert_(0.0 <= cy <= 1.0, f"cy out of range: {cy}")
            assert_(0.0 <= conf <= 1.0, f"conf out of range: {conf}")
            print(f"    -> detector returned: cx={cx:.2f}, cy={cy:.2f}, conf={conf:.2f}")
        else:
            print("    -> detector returned None (letterbox fallback will trigger)")

    return t["passed"]


if __name__ == "__main__":
    asyncio.run(main())
