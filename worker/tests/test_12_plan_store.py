"""Test 12: Smart reel storage helpers + migration 005 (offline).

Uses a small in-memory fake of the Supabase client, so no database is
touched. Also checks the migration file and schema.sql for drift.
"""
import os
import sys

from _helpers import test_case, assert_, section

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MIGRATION = os.path.join(_ROOT, "db", "migrations", "005_smart_reel.sql")
_SCHEMA = os.path.join(_ROOT, "db", "schema.sql")


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    """Mimics the supabase-py call chain used by plan_store."""

    def __init__(self, db, table):
        self.db, self.table_name = db, table
        self.op, self.payload, self.columns = None, None, None
        self.filters, self._negate, self._limit = [], False, None

    def select(self, columns):
        self.op, self.columns = "select", columns
        return self

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def update(self, values):
        self.op, self.payload = "update", values
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val, False))
        return self

    @property
    def not_(self):
        self._negate = True
        return self

    def is_(self, col, val):
        assert val == "null"
        self.filters.append(("null", col, None, self._negate))
        self._negate = False
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _match(self, row):
        for kind, col, val, neg in self.filters:
            ok = (row.get(col) == val) if kind == "eq" else (row.get(col) is None)
            if kind == "null" and neg:
                ok = not ok
            if not ok:
                return False
        return True

    def execute(self):
        rows = self.db.tables.setdefault(self.table_name, [])
        if self.op == "insert":
            rows.append(dict(self.payload))
            return _Result([dict(self.payload)])
        hit = [r for r in rows if self._match(r)]
        if self.op == "update":
            for r in hit:
                r.update(self.payload)
            return _Result([dict(r) for r in hit])
        if self.op == "delete":
            self.db.tables[self.table_name] = [r for r in rows if r not in hit]
            return _Result([dict(r) for r in hit])
        out = hit[: self._limit] if self._limit else hit
        return _Result([dict(r) for r in out])


class FakeDB:
    def __init__(self, **tables):
        self.tables = {k: [dict(r) for r in v] for k, v in tables.items()}

    def table(self, name):
        return _Query(self, name)


PLAN = {"shape": "montage", "reason": "Because.", "beats": [{"from_id": "S1", "to_id": "S2"}]}
SEGS = [{"start_time": 1.0, "end_time": 9.5, "title": "a", "reason": ""},
        {"start_time": 40.0, "end_time": 52.0, "title": "b", "reason": ""}]


async def main() -> bool:
    section("Test 12: Smart reel storage + migration 005")
    from worker.services.plan_store import (
        load_plan, load_sentences, save_plan, save_sentences, update_verifier,
    )
    from worker.text.sentences import build_sentences

    results = []

    with test_case("sentences save and load back unchanged (objects accepted)") as t:
        db = FakeDB(transcripts=[{"video_id": "v1", "full_text": "hi", "sentences": None}])
        words = [{"text": w, "start": i * 0.3, "end": i * 0.3 + 0.25}
                 for i, w in enumerate("Hello there my friend. How are you today?".split())]
        sents = build_sentences(words)
        save_sentences("v1", sents, client=db)
        got = load_sentences("v1", client=db)
        assert_(got == [s.to_dict() for s in sents], f"{got}")
        assert_(got[0]["id"] == "S1", "ids")
    results.append(t["passed"])

    with test_case("saving sentences without a transcript row is an error") as t:
        try:
            save_sentences("nope", [{"id": "S1"}], client=FakeDB(transcripts=[]))
            raise AssertionError("expected RuntimeError")
        except RuntimeError:
            pass
    results.append(t["passed"])

    with test_case("load_sentences gives None when missing or never saved") as t:
        assert_(load_sentences("v1", client=FakeDB(transcripts=[])) is None, "no row")
        db = FakeDB(transcripts=[{"video_id": "v1", "sentences": None}])
        assert_(load_sentences("v1", client=db) is None, "null column")
    results.append(t["passed"])

    with test_case("save_plan writes one accepted row usable by the render stage") as t:
        db = FakeDB(edit_plans=[])
        save_plan("v1", PLAN, SEGS, verifier={"verdict": "pass"}, client=db)
        rows = db.tables["edit_plans"]
        assert_(len(rows) == 1, f"{len(rows)} rows")
        r = rows[0]
        assert_(r["video_id"] == "v1" and r["candidate_index"] == 0, f"{r}")
        assert_(r["status"] == "accepted" and r["shape"] == "montage", f"{r}")
        assert_(r["segments"] == SEGS and r["plan"] == PLAN, f"{r}")
        assert_(r["verifier"] == {"verdict": "pass"}, f"{r}")
    results.append(t["passed"])

    with test_case("saving again replaces the smart plan; old candidates are untouched") as t:
        old = {"video_id": "v1", "candidate_index": 3, "segments": [], "status": "pending_review", "plan": None}
        other = {"video_id": "v2", "candidate_index": 0, "segments": [], "status": "accepted", "plan": {"shape": "montage"}}
        db = FakeDB(edit_plans=[old, other])
        save_plan("v1", PLAN, SEGS, client=db)
        save_plan("v1", dict(PLAN, shape="single_stretch"), SEGS, client=db)
        rows = db.tables["edit_plans"]
        mine = [r for r in rows if r["video_id"] == "v1" and r["plan"] is not None]
        assert_(len(mine) == 1 and mine[0]["shape"] == "single_stretch", f"{mine}")
        assert_(old in rows, "old candidate row was deleted")
        assert_(other in rows, "another video's plan was deleted")
    results.append(t["passed"])

    with test_case("segments the render stage cannot read are rejected before any write") as t:
        db = FakeDB(edit_plans=[])
        for bad in ([{"start": 1.0, "end": 9.0}], [{"start_time": 5.0, "end_time": 5.0}],
                    [{"start_time": -1.0, "end_time": 3.0}], [{"start_time": "x", "end_time": 3.0}]):
            try:
                save_plan("v1", PLAN, bad, client=db)
                raise AssertionError(f"expected ValueError for {bad!r}")
            except ValueError:
                pass
        assert_(db.tables["edit_plans"] == [], "a row was written")
    results.append(t["passed"])

    with test_case("an invalid shape is rejected before any write") as t:
        db = FakeDB(edit_plans=[])
        for bad in ({"reason": "x"}, {"shape": "carousel"}):
            try:
                save_plan("v1", bad, SEGS, client=db)
                raise AssertionError("expected ValueError")
            except ValueError:
                pass
        assert_(db.tables["edit_plans"] == [], "a row was written")
    results.append(t["passed"])

    with test_case("update_verifier and load_plan touch only the smart plan row") as t:
        old = {"video_id": "v1", "candidate_index": 3, "segments": [], "status": "pending_review", "plan": None, "verifier": None}
        db = FakeDB(edit_plans=[old])
        assert_(load_plan("v1", client=db) is None, "old-style row must not count as a plan")
        save_plan("v1", PLAN, SEGS, client=db)
        update_verifier("v1", {"verdict": "revise"}, client=db)
        got = load_plan("v1", client=db)
        assert_(got["verifier"] == {"verdict": "revise"} and got["shape"] == "montage", f"{got}")
        assert_(got["segments"] == SEGS, f"{got}")
        assert_(old["verifier"] is None and db.tables["edit_plans"][0]["verifier"] is None, "old row changed")
    results.append(t["passed"])

    with test_case("real Supabase builders produce the 'not null' filter (nothing is sent)") as t:
        from supabase import create_client

        real = create_client("https://example.supabase.co", "sb_secret_dummy_key_for_offline_test")
        req = real.table("edit_plans").delete().eq("video_id", "v1").not_.is_("plan", "null")
        params = str(req.request.params)
        assert_("plan" in params and "not.is.null" in params, params)
        assert_("video_id" in params and "eq.v1" in params, params)
        req = real.table("edit_plans").update({"verifier": {}}).eq("video_id", "v1").not_.is_("plan", "null")
        assert_("not.is.null" in str(req.request.params), str(req.request.params))
    results.append(t["passed"])

    with test_case("migration 005 is idempotent and matches schema.sql") as t:
        sql = open(_MIGRATION, encoding="utf-8").read().lower()
        schema = open(_SCHEMA, encoding="utf-8").read().lower()
        for col in ("sentences", "plan", "shape", "verifier"):
            assert_(f"add column if not exists {col}" in sql.replace("\n", " ").replace("  ", " "), f"{col} not idempotent")
        assert_("drop constraint if exists edit_plans_shape_check" in sql, "constraint not re-runnable")
        for needle in ("sentences jsonb", "plan jsonb", "shape text", "verifier jsonb", "edit_plans_shape_check"):
            assert_(needle in schema, f"schema.sql missing {needle}")
        assert_("drop table" not in sql and "delete from" not in sql, "destructive statement")
    results.append(t["passed"])

    return all(results)


if __name__ == "__main__":
    import asyncio

    sys.exit(0 if asyncio.run(main()) else 1)
