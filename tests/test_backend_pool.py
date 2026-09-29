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
