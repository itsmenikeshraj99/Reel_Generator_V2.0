"""Test 08: Heartbeat subprocess runner.

Verifies `Pipeline._run_with_heartbeat` returns promptly when the
command finishes, does not deadlock on large stderr output, reports
failures and timeouts with the expected messages, and sends heartbeats
at the configured interval. Offline: Python itself stands in for ffmpeg
and the database heartbeat is replaced by a counter.
"""
import sys
import time

from _helpers import test_case, assert_, section


def _make_pipeline():
    from worker.pipeline import Pipeline

    pipe = Pipeline("test-heartbeat-runner")
    beats = {"n": 0}

    async def fake_heartbeat() -> None:
        beats["n"] += 1

    pipe._heartbeat = fake_heartbeat  # no database call
    return pipe, beats


def _py(code: str) -> list:
    return [sys.executable, "-c", code]


async def main() -> bool:
    section("Test 08: Heartbeat subprocess runner")
    import worker.pipeline as pipeline_mod

    results = []

    with test_case("quick command returns within 10s (not after a 60s wait)") as t:
        pipe, _ = _make_pipeline()
        t0 = time.time()
        await pipe._run_with_heartbeat(_py("pass"))
        elapsed = time.time() - t0
        assert_(elapsed < 10, f"took {elapsed:.1f}s")
    results.append(t["passed"])

    with test_case("large stderr output (~2 MB) does not hang") as t:
        pipe, _ = _make_pipeline()
        code = "import sys; sys.stderr.write('x' * 2_000_000); sys.stdout.write('y' * 2_000_000)"
        t0 = time.time()
        await pipe._run_with_heartbeat(_py(code), timeout=30)
        elapsed = time.time() - t0
        assert_(elapsed < 20, f"took {elapsed:.1f}s")
    results.append(t["passed"])

    with test_case("failing command raises with return code and stderr tail") as t:
        pipe, _ = _make_pipeline()
        code = "import sys; sys.stderr.write('boom-message'); sys.exit(3)"
        try:
            await pipe._run_with_heartbeat(_py(code), timeout=30)
            raise AssertionError("expected RuntimeError")
        except RuntimeError as exc:
            msg = str(exc)
            assert_("Subprocess failed" in msg and "rc=3" in msg, f"bad message: {msg}")
            assert_("boom-message" in msg, f"stderr tail missing: {msg}")
    results.append(t["passed"])

    with test_case("slow command is killed and reports the timeout") as t:
        pipe, _ = _make_pipeline()
        t0 = time.time()
        try:
            await pipe._run_with_heartbeat(_py("import time; time.sleep(30)"), timeout=2)
            raise AssertionError("expected RuntimeError")
        except RuntimeError as exc:
            assert_("timed out after 2s" in str(exc), f"bad message: {exc}")
        elapsed = time.time() - t0
        assert_(elapsed < 10, f"took {elapsed:.1f}s")
    results.append(t["passed"])

    with test_case("heartbeat is sent at the configured interval") as t:
        pipe, beats = _make_pipeline()
        original = getattr(pipeline_mod, "HEARTBEAT_INTERVAL_S", None)
        pipeline_mod.HEARTBEAT_INTERVAL_S = 1
        try:
            await pipe._run_with_heartbeat(_py("import time; time.sleep(3.5)"), timeout=30)
        finally:
            if original is None:
                del pipeline_mod.HEARTBEAT_INTERVAL_S
            else:
                pipeline_mod.HEARTBEAT_INTERVAL_S = original
        assert_(beats["n"] >= 2, f"only {beats['n']} heartbeat(s)")
    results.append(t["passed"])

    return all(results)


if __name__ == "__main__":
    import asyncio

    sys.exit(0 if asyncio.run(main()) else 1)
