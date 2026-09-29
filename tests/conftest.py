import contextlib
import sys
from pathlib import Path

import pytest

from pbix2html import query

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _build_fixture_pbix(tmp_path_factory, prefix: str, script: str, filename: str) -> Path:
    """Runs one of the tests/fixtures/make_fake_*.py generators inside a temp folder
    (they write their .pbix to the current directory) and returns the file."""
    d = tmp_path_factory.mktemp(prefix)
    with contextlib.chdir(d):
        exec(compile((ROOT / "tests/fixtures" / script).read_text(encoding="utf-8"), script, "exec"), {})
    return d / filename


@pytest.fixture(scope="session")
def fake_pbix(tmp_path_factory) -> Path:
    """The synthetic classic-format .pbix."""
    return _build_fixture_pbix(tmp_path_factory, "pbix", "make_fake_pbix.py", "Executive_Dashboard.pbix")


@pytest.fixture(scope="session")
def fake_pbir_pbix(tmp_path_factory) -> Path:
    """The synthetic PBIR-format .pbix."""
    return _build_fixture_pbix(tmp_path_factory, "pbir", "make_fake_pbir_pbix.py", "Dashboard_PBIR.pbix")


@pytest.fixture(autouse=True)
def _cwd_root(monkeypatch):
    monkeypatch.chdir(ROOT)


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """query.py's on-disk cache uses a relative `cache/` dir; combined with `_cwd_root`
    pinning every test's cwd to the repo root above, that's the same real folder on disk
    across every test and every run -- a stale entry left by one test (or a previous
    full run) can silently change a later test's result. Confirmed: running the full
    suite twice in a row (without cleaning `cache/` in between) made
    test_serve.py::test_visual_endpoint_uses_proxy_user fail on the second run with
    `be.calls[-1]` raising IndexError -- it hit the leftover cache entry and never
    called the backend at all. Redirecting CACHE_DIR to a fresh tmp_path per test
    closes this for every test file, not just ones that happen to work around it."""
    monkeypatch.setattr(query, "CACHE_DIR", tmp_path / "cache")
