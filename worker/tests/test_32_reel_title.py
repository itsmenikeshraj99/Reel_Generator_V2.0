"""Test 32: The title saved with a reel (worker/stages/naming.py + pipeline hook). No network, no cost."""
import inspect
import sys
from types import SimpleNamespace
from unittest.mock import patch

from _helpers import test_case, assert_, section


class FakeDB:
    def __init__(self, rows=None, boom=False):
        self.rows, self.boom = rows, boom

    def table(self, name):
        db = self

        class Q:
            def select(self, *a): return self
            def eq(self, *a): return self
            def order(self, *a): return self
            def limit(self, *a): return self

            def execute(self):
                if db.boom:
                    raise RuntimeError("db down")
                return SimpleNamespace(data=db.rows)

        return Q()


async def main() -> bool:
    section("Test 32: Reel title")
    from worker.stages import naming
    from worker import pipeline as pl

    results = []

    def title_with(db):
        with patch.object(naming, "supabase_client", db):
            return naming.reel_title("v1")

    with test_case("the planner's hook title is used") as t:
        assert_(title_with(FakeDB([{"plan": {"title": "Why Sleep Matters"}}])) == "Why Sleep Matters", "title")
    results.append(t["passed"])

    with test_case("the title is cleaned (quotes, symbols, length) like the on-screen one") as t:
        got = title_with(FakeDB([{"plan": {"title": '"Sleep {is} the #1 skill"  ' + "x" * 80}}]))
        assert_(len(got) <= 50 and "{" not in got and not got.startswith('"'), f"{got!r}")
    results.append(t["passed"])

    with test_case("a Hindi title is kept as it is") as t:
        assert_(title_with(FakeDB([{"plan": {"title": "भारत की कहानी"}}])) == "भारत की कहानी", "hindi")
    results.append(t["passed"])

    with test_case("no plan, no title, or a database error falls back to the old title and never raises") as t:
        for db in (FakeDB([]), FakeDB([{"plan": None}]), FakeDB([{"plan": {"title": "   "}}]), FakeDB(boom=True)):
            assert_(title_with(db) == naming.DEFAULT_TITLE == "AI Generated Reel", "fallback")
    results.append(t["passed"])

    with test_case("the render stage saves that title with the reel") as t:
        src = inspect.getsource(pl.Pipeline._stage_render)
        assert_('"title": reel_title(self.video_id)' in src, "pipeline uses reel_title")
        assert_('"title": "AI Generated Reel"' not in src, "no hard-coded title left")
    results.append(t["passed"])

    return all(results)


if __name__ == "__main__":
    import asyncio

    sys.exit(0 if asyncio.run(main()) else 1)
