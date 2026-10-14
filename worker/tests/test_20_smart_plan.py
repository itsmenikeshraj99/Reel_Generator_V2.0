"""Test 20: Smart pipeline wiring (worker/worker/stages/smart_plan.py + pipeline flag).

Offline: speech-to-text, Gemini, the database and ffmpeg are fakes. No network, no cost.
"""
import os
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from _helpers import test_case, assert_, section


class _Res:
    def __init__(self, data):
        self.data = data


class _Q:
    def __init__(self, db, table):
        self.db, self.t, self.op, self.payload, self.filters = db, table, None, None, []

    def select(self, *_):
        self.op = "select"
        return self

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def upsert(self, row, on_conflict=None):
        self.op, self.payload = "upsert", row
        return self

    def update(self, values):
        self.op, self.payload = "update", values
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, c, v):
        self.filters.append((c, v))
        return self

    @property
    def not_(self):
        return self

    def is_(self, *_):
        return self

    def limit(self, _):
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.t, [])
        hit = [r for r in rows if all(r.get(c) == v for c, v in self.filters)]
        if self.op == "select":
            return _Res([dict(r) for r in hit])
        if self.op == "insert":
            rows.append(dict(self.payload))
            return _Res([dict(self.payload)])
        if self.op == "upsert":
            old = [r for r in rows if r.get("video_id") == self.payload.get("video_id")]
            if old:
                old[0].update(self.payload)
            else:
                rows.append(dict(self.payload))
            return _Res([dict(self.payload)])
        if self.op == "update":
            for r in hit:
                r.update(self.payload)
            return _Res([dict(r) for r in hit])
        self.db.tables[self.t] = [r for r in rows if r not in hit]
        return _Res([])


class FakeDB:
    def __init__(self, **tables):
        self.tables = {k: [dict(r) for r in v] for k, v in tables.items()}

    def table(self, name):
        return _Q(self, name)


def speech_words(seconds=60):
    """Evenly spaced words with a full stop every 5th word -> many sentences."""
    words, t = [], 0.0
    while t < seconds:
        i = len(words)
        words.append({"text": "word." if i % 5 == 4 else "word", "start": round(t, 3), "end": round(t + 0.35, 3)})
        t += 0.45
    return words


class FakePipe:
    def __init__(self, vid="vid-1"):
        self.video_id = vid
        self.smart_state, self.reel_meta_extra = {}, {}
        self.beats = 0
        self.runner_calls = []

    async def _heartbeat(self):
        self.beats += 1

    async def _get_video_duration(self, path):
        return 120.0

    async def _run_with_heartbeat(self, cmd, timeout=600):
        self.runner_calls.append(cmd)


class FakeFiles:
    def __init__(self):
        self.deleted = []

    def delete(self, name):
        self.deleted.append(name)


async def main() -> bool:
    section("Test 20: Smart pipeline wiring")
    from worker.asr.base import TranscriptResult, Word
    from worker.config import settings
    from worker.edit.select_reel import Selection
    from worker.gemini.understand import Understanding
    from worker.pipeline import NonRetryableError, Pipeline
    from worker.stages import smart_plan as sp

    results = []
    understanding = Understanding("English", "s", [{"title": "Idea", "from_id": "S1", "to_id": "S3"}], [], [])

    def asr_result(words):
        return TranscriptResult("groq", " ".join(w["text"] for w in words),
                                [Word(w["text"], w["start"], w["end"]) for w in words], language="English")

    class Env:
        """Patches everything the stage talks to; records what it was asked."""

        def __init__(self, db=None, words=None, asr_error=None, understand_error=None):
            self.db = db or FakeDB(transcripts=[])
            self.words = words if words is not None else speech_words(60)
            self.asr_error, self.understand_error = asr_error, understand_error
            self.asr_calls, self.files = 0, FakeFiles()
            self.saved_plans = []

        def patches(self):
            env = self

            async def fake_asr(path):
                env.asr_calls += 1
                if env.asr_error:
                    raise env.asr_error
                return asr_result(env.words), {"provider": "groq", "fallback_used": False, "primary_error": None}

            async def fake_understand(uri, sentences, video_id="x"):
                if env.understand_error:
                    raise env.understand_error
                return understanding

            async def fake_wait(f):
                return f

            up = SimpleNamespace(uri="files/up1", name="files/up1")
            return [
                patch.object(sp, "supabase_client", self.db),
                patch.object(sp, "transcribe_with_fallback", fake_asr),
                patch.object(sp, "understand", fake_understand),
                patch.object(sp.gemini_client, "upload_video", lambda p: up),
                patch.object(sp.gemini_client, "wait_for_processing", fake_wait),
                patch.object(sp.gemini_client, "client", SimpleNamespace(files=self.files)),
                patch.object(sp, "save_sentences", lambda vid, sents: self._save_sentences(vid, sents)),
            ]

        def _save_sentences(self, vid, sents):
            for r in self.db.tables.setdefault("transcripts", []):
                if r.get("video_id") == vid:
                    r["sentences"] = [s.to_dict() for s in sents]
                    return
            raise RuntimeError("no transcript row")

    async def run_with(env, coro_fn):
        ps = env.patches()
        for p in ps:
            p.start()
        try:
            return await coro_fn()
        finally:
            for p in reversed(ps):
                p.stop()

    with test_case("flag is off by default and the classic stages run when it is off") as t:
        assert_(settings.SMART_PIPELINE_ENABLED is False, "default must be off")
        from worker.stages import review as review_mod, transcribe_plan as tp_mod

        marks = []

        async def old_tp(*a, **k):
            marks.append("classic-transcribe")
            return True

        async def old_review(*a, **k):
            marks.append("classic-review")
            return True

        async def boom(*a, **k):
            raise AssertionError("smart stage must not run when the flag is off")

        with patch.object(tp_mod, "transcribe_and_plan", old_tp), patch.object(review_mod, "review_candidates", old_review), \
                patch.object(sp, "run_transcribe_understand", boom), patch.object(sp, "run_select", boom):
            p = Pipeline("v-off")
            assert_(await p._stage_transcribe_plan("x.mp4") is True, "transcribe")
            assert_(await p._stage_review("x.mp4") is True and await p._stage_review() is True, "review (with and without path)")
        assert_(marks == ["classic-transcribe", "classic-review", "classic-review"], f"{marks}")
        assert_(p._reel_meta("full", True) == {"quality": "full", "captioned": True}, f"{p._reel_meta('full', True)}")
    results.append(t["passed"])

    with test_case("with the flag on, the pipeline hands both AI stages to the smart ones") as t:
        marks = []

        async def smart_tp(pipe, path):
            marks.append(("tp", path))
            return True

        async def smart_sel(pipe, path):
            marks.append(("sel", path))
            return True

        with patch.object(settings, "SMART_PIPELINE_ENABLED", True), \
                patch.object(sp, "run_transcribe_understand", smart_tp), patch.object(sp, "run_select", smart_sel):
            p = Pipeline("v-on")
            await p._stage_transcribe_plan("src.mp4")
            await p._stage_review("src.mp4")
        assert_(marks == [("tp", "src.mp4"), ("sel", "src.mp4")], f"{marks}")
    results.append(t["passed"])

    with test_case("smart metrics are merged into reels.meta without losing the usual keys") as t:
        p = Pipeline("v-meta")
        p.reel_meta_extra = {"smart_pipeline": True, "needs_review": False, "smart": {"shape": "montage"}}
        m = p._reel_meta("raw_cut", False)
        assert_(m["quality"] == "raw_cut" and m["captioned"] is False and m["smart"]["shape"] == "montage"
                and m["smart_pipeline"] is True, f"{m}")
    results.append(t["passed"])

    with test_case("transcribe+understand: saves transcript and sentences, keeps state, deletes the Gemini upload") as t:
        env, pipe = Env(), FakePipe()
        ok = await run_with(env, lambda: sp.run_transcribe_understand(pipe, "src.mp4"))
        row = env.db.tables["transcripts"][0]
        assert_(ok and row["video_id"] == "vid-1" and len(row["words"]) > 100 and len(row["sentences"]) >= 5, f"{row.keys()}")
        assert_(row["words"][0].keys() == {"text", "start", "end"}, f"{row['words'][0]}")
        assert_(pipe.smart_state["understanding"] is understanding and len(pipe.smart_state["sentences"]) == len(row["sentences"]), "state")
        m = pipe.smart_state["metrics"]
        assert_(m["asr"]["provider"] == "groq" and m["sentences"] == len(row["sentences"]) and m["key_points"] == 1, f"{m}")
        assert_(env.files.deleted == ["files/up1"] and pipe.beats >= 2, f"{env.files.deleted} {pipe.beats}")
    results.append(t["passed"])

    with test_case("a stored transcript is reused and the speech-to-text is not called again") as t:
        words = speech_words(60)
        env = Env(db=FakeDB(transcripts=[{"video_id": "vid-1", "full_text": "stored text", "words": words}]))
        pipe = FakePipe()
        await run_with(env, lambda: sp.run_transcribe_understand(pipe, "src.mp4"))
        assert_(env.asr_calls == 0 and pipe.smart_state["metrics"]["asr"]["provider"] == "reused", f"{env.asr_calls}")
    results.append(t["passed"])

    with test_case("a second call in the same run does nothing (stage retry after a later failure)") as t:
        env, pipe = Env(), FakePipe()
        await run_with(env, lambda: sp.run_transcribe_understand(pipe, "src.mp4"))
        calls = env.asr_calls
        await run_with(env, lambda: sp.run_transcribe_understand(pipe, "src.mp4"))
        assert_(env.asr_calls == calls == 1, f"{env.asr_calls}")
    results.append(t["passed"])

    with test_case("no speech, or too little speech, is a permanent (non-retryable) failure") as t:
        for words in ([], speech_words(8)):
            env = Env(words=words)
            try:
                await run_with(env, lambda: sp.run_transcribe_understand(FakePipe(), "src.mp4"))
                raise AssertionError("expected NonRetryableError")
            except NonRetryableError as exc:
                assert_("speech" in str(exc).lower(), str(exc))
    results.append(t["passed"])

    with test_case("a transcription outage is retryable; a failed understanding call still deletes the upload") as t:
        env = Env(asr_error=RuntimeError("both providers down"))
        try:
            await run_with(env, lambda: sp.run_transcribe_understand(FakePipe(), "src.mp4"))
            raise AssertionError("expected an error")
        except NonRetryableError:
            raise AssertionError("an outage must be retryable")
        except RuntimeError:
            pass
        from worker.gemini.understand import UnderstandingError

        env = Env(understand_error=UnderstandingError("no usable key points"))
        try:
            await run_with(env, lambda: sp.run_transcribe_understand(FakePipe(), "src.mp4"))
            raise AssertionError("expected an error")
        except NonRetryableError:
            raise AssertionError("a bad answer is retryable")
        except RuntimeError as exc:
            assert_("key points" in str(exc), str(exc))
        assert_(env.files.deleted == ["files/up1"], f"{env.files.deleted}")
    results.append(t["passed"])

    with test_case("select: saves the plan in the render stage's shape and records metrics for reels.meta") as t:
        env, pipe = Env(), FakePipe()
        seen = {}

        async def fake_select(**kw):
            seen.update(kw)
            seg = await kw["make_draft"]([{"start_time": 1.0, "end_time": 9.0, "title": "t", "reason": ""}])
            seen["draft"] = seg
            return Selection(ok=True, plan={"shape": "montage", "beats": [{}, {}, {}], "reason": "r"},
                             segments=[{"start_time": 1.0, "end_time": 9.0, "title": "t", "reason": ""}],
                             verifier={"verdict": "pass"}, needs_review=False, revisions=1, total_seconds=31.0,
                             history=["attempt 1: verdict revise", "attempt 2: verdict pass"],
                             attempts=[{"attempt": 1, "verdict": {"verdict": "revise", "fixes": ["f"]}},
                                       {"attempt": 2, "verdict": {"verdict": "pass"}}])

        async def fake_silences(path, dur=None):
            return [(2.0, 2.5)]

        async def fake_render(video, segs, out, runner=None):
            await runner(["ffmpeg", "fake"])
            open(out, "wb").write(b"x")
            return out

        saved = []

        async def go():
            await sp.run_transcribe_understand(pipe, "src.mp4")
            return await sp.run_select(pipe, "src.mp4")

        with patch.object(sp, "select_reel", fake_select), patch.object(sp, "detect_silences", fake_silences), \
                patch.object(sp, "render_draft", fake_render), \
                patch.object(sp, "save_plan", lambda vid, plan, segs, ver: saved.append((vid, plan, segs, ver))):
            ok = await run_with(env, go)
        assert_(ok and len(saved) == 1, f"{saved}")
        vid, plan, segs, ver = saved[0]
        assert_(vid == "vid-1" and plan["shape"] == "montage" and segs[0]["start_time"] == 1.0 and ver == {"verdict": "pass"}, f"{saved[0]}")
        assert_(seen["max_revisions"] == settings.MAX_REVISIONS and seen["target_seconds"] == settings.REEL_TARGET_SECONDS, "settings")
        assert_(seen["silences"] == [(2.0, 2.5)] and seen["media_duration"] == 120.0, "silences / duration")
        assert_(pipe.runner_calls == [["ffmpeg", "fake"]], f"draft must use the heartbeat runner: {pipe.runner_calls}")
        extra = pipe.reel_meta_extra
        assert_([x["verdict"]["verdict"] for x in extra["smart"]["attempts"]] == ["revise", "pass"], "attempts missing from meta")
        assert_(extra["smart_pipeline"] is True and extra["needs_review"] is False and extra["smart"]["revisions"] == 1
                and extra["smart"]["verdict"] == "pass" and extra["smart"]["asr"]["provider"] == "groq", f"{extra}")
        if os.path.exists(seen["draft"]):
            os.remove(seen["draft"])
    results.append(t["passed"])

    with test_case("select: needs_review is passed through; no plan at all is a permanent failure") as t:
        env, pipe = Env(), FakePipe()

        async def flagged(**kw):
            return Selection(ok=True, plan={"shape": "single_stretch", "beats": [{}], "reason": "r"},
                             segments=[{"start_time": 0.0, "end_time": 25.0}], verifier=None,
                             needs_review=True, used_fallback=True, total_seconds=25.0)

        async def nothing(**kw):
            return Selection(ok=False, history=["attempt 1: planner gave no valid plan"])

        async def silences(path, dur=None):
            raise RuntimeError("ffmpeg missing")

        async def go():
            await sp.run_transcribe_understand(pipe, "src.mp4")
            return await sp.run_select(pipe, "src.mp4")

        with patch.object(sp, "select_reel", flagged), patch.object(sp, "detect_silences", silences), \
                patch.object(sp, "save_plan", lambda *a: None):
            await run_with(env, go)
        assert_(pipe.reel_meta_extra["needs_review"] is True and pipe.reel_meta_extra["smart"]["used_fallback"] is True, f"{pipe.reel_meta_extra}")
        assert_(pipe.smart_state["metrics"]["silence_detection"] == "failed", "silence failure should be noted, not fatal")

        env, pipe = Env(), FakePipe()

        async def go2():
            await sp.run_transcribe_understand(pipe, "src.mp4")
            return await sp.run_select(pipe, "src.mp4")

        with patch.object(sp, "select_reel", nothing), patch.object(sp, "detect_silences", silences):
            try:
                await run_with(env, go2)
                raise AssertionError("expected NonRetryableError")
            except NonRetryableError as exc:
                assert_("planner gave no valid plan" in str(exc), str(exc))
    results.append(t["passed"])

    with test_case("select without a prior understanding runs that first; a save failure mentions migration 005") as t:
        env, pipe = Env(), FakePipe()

        async def ok_select(**kw):
            return Selection(ok=True, plan={"shape": "montage", "beats": [{}, {}, {}], "reason": "r"},
                             segments=[{"start_time": 0.0, "end_time": 25.0}], verifier={"verdict": "pass"}, total_seconds=25.0)

        async def silences(path, dur=None):
            return []

        def failing_save(*a):
            raise RuntimeError("column edit_plans.plan does not exist")

        with patch.object(sp, "select_reel", ok_select), patch.object(sp, "detect_silences", silences), \
                patch.object(sp, "save_plan", failing_save):
            try:
                await run_with(env, lambda: sp.run_select(pipe, "src.mp4"))
                raise AssertionError("expected RuntimeError")
            except NonRetryableError:
                raise AssertionError("a save failure must be retryable")
            except RuntimeError as exc:
                assert_("migration 005" in str(exc), str(exc))
        assert_(env.asr_calls == 1 and pipe.smart_state.get("understanding") is understanding, "stage 1 should have run first")
    results.append(t["passed"])

    return all(results)


if __name__ == "__main__":
    import asyncio

    sys.exit(0 if asyncio.run(main()) else 1)
