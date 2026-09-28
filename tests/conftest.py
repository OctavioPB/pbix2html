import json, os, sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture(scope="session")
def fake_pbix(tmp_path_factory) -> Path:
    """Generates the synthetic .pbix and returns its path."""
    d = tmp_path_factory.mktemp("pbix")
    cwd = os.getcwd(); os.chdir(d)
    try:
        exec(compile((ROOT / "tests/fixtures/make_fake_pbix.py").read_text(), "make_fake", "exec"), {})
    finally:
        os.chdir(cwd)
    return d / "Dashboard_Ejecutivo.pbix"


@pytest.fixture(autouse=True)
def _cwd_root(monkeypatch):
    monkeypatch.chdir(ROOT)
