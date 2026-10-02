"""TeradataBackend: one query per session at a time, identity cleared, bad sessions discarded."""
import threading
import time

import pytest

from pbix2html import query


class FakeCon:
    live = 0
    def __init__(self, log, fail_on=None):
        self.log, self.fail_on, self.closed, self.busy = log, fail_on, False, False
        self.band = None

    def cursor(self):
        return self

    def __enter__(self):
        assert not self.busy, "two requests on one session at once"
        self.busy = True
        return self

    def __exit__(self, *a):
        self.busy = False

    def execute(self, sql, values=None):
        if sql.startswith("SET QUERY_BAND = NONE"):
            self.band = None
        elif sql.startswith("SET QUERY_BAND"):
            self.band = sql.split("PROXYUSER=")[1].split(";")[0]
        else:
            time.sleep(0.01)
            if self.fail_on and self.fail_on in sql:
                raise RuntimeError("boom")
            self.log.append((threading.get_ident(), self.band, sql))
            self.description = [("V",)]

    def fetchall(self):
        return [(1,)]

    def fetchmany(self, n=1):          # DB-API: the backend caps rows with this (settings.max_rows)
        return self.fetchall()[:n]

    def close(self):
        self.closed = True


def backend(monkeypatch, fail_on=None):
    log, made = [], []
    b = query.TeradataBackend(max_connections=3)
    def connect():
        con = FakeCon(log, fail_on)
        made.append(con)
        return con
    monkeypatch.setattr(b, "_connect", connect)
    return b, log, made


def test_concurrent_requests_never_share_a_session_or_an_identity(monkeypatch):
    b, log, made = backend(monkeypatch)
    errors = []
    def run(user):
        try:
            for _ in range(5):
                b.execute(f"SELECT '{user}'", [], user)
        except Exception as e:                      # noqa: BLE001
            errors.append(e)
    threads = [threading.Thread(target=run, args=(f"u{i}",)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert all(band == sql.split("'")[1] for _, band, sql in log)      # each query ran as its own user
    assert len(made) <= 3 and all(c.band is None for c in made)        # capped, identity cleared


def test_a_failing_query_discards_its_session_and_the_next_request_reconnects(monkeypatch):
    b, log, made = backend(monkeypatch, fail_on="BAD")
    b.execute("SELECT 1", [], "u1")
    with pytest.raises(RuntimeError):
        b.execute("SELECT BAD", [], "u1")
    assert made[0].closed                                              # not returned to the pool
    b.execute("SELECT 2", [], "u2")
    assert len(made) == 2 and log[-1][1] == "u2"


class _RowsCon:
    """A cursor that returns `n` rows, so the row cap can be exercised without Teradata."""

    def __init__(self, n):
        self.n = n
        self.description = [("a",), ("b",)]

    def cursor(self):
        return self

    def execute(self, *a):
        pass

    def fetchall(self):
        return [(i, i * 2) for i in range(self.n)]

    def fetchmany(self, k=1):
        return self.fetchall()[:k]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass


@pytest.mark.parametrize("rows, cap, expect_rows, expect_flag", [
    (10, 5, 5, 5),      # more than the cap: cut, and the visual is told
    (5, 5, 5, None),    # exactly the cap is NOT truncated (one row past it is fetched to tell)
    (3, 5, 3, None),
    (10, 0, 10, None),  # 0 disables the cap
])
def test_a_huge_result_is_capped_and_says_so(rows, cap, expect_rows, expect_flag, monkeypatch):
    """One row becomes one `<tr>`, so a matrix grouped by several dimensions can return far more
    than a browser will draw and the visual simply never appears. Rows past `MAX_ROWS` are not
    fetched, and the block carries `truncated` so the HTML can say so — a capped table must never
    read as the whole answer. The grand-total row is computed by the database over the full query
    (ADR-010), so it stays correct."""
    import dataclasses

    monkeypatch.setattr(query, "settings", dataclasses.replace(query.settings, max_rows=cap))
    backend = query.TeradataBackend(max_connections=1)
    backend._idle = [_RowsCon(rows)]
    block = backend.execute("SELECT a, b FROM t", [])
    assert len(block["rows"]) == expect_rows
    assert block.get("truncated") == expect_flag
