"""Panel routes, isolated from the real reports/metrics/out via monkeypatched dirs."""
import shutil

from fastapi.testclient import TestClient

from pbix2html import gui, semantic


def _client(tmp_path, monkeypatch, fake_pbix):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    shutil.copy(fake_pbix, reports_dir / fake_pbix.name)
    monkeypatch.setattr(gui, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(gui, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(semantic, "METRICS_DIR", tmp_path / "metrics")
    return TestClient(gui.app), fake_pbix.stem


def test_table_map_shows_entities_after_extract(tmp_path, monkeypatch, fake_pbix):
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    c.post(f"/reports/{name}/extract")
    r = c.get(f"/reports/{name}/table-map")
    assert r.status_code == 200 and "Sales" in r.text and "Region" in r.text


def test_table_map_rejects_unsafe_query(tmp_path, monkeypatch, fake_pbix):
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    c.post(f"/reports/{name}/extract")
    r = c.post(f"/reports/{name}/table-map", data={"td__Sales": "DELETE FROM sales_fact"})
    assert r.status_code == 200
    assert "Mapping not saved" in r.text
    assert "DELETE" in r.text
    assert not (tmp_path / "metrics" / f"{name}.table_map.json").exists()


def test_table_map_accepts_read_only_query_and_feeds_scaffold(tmp_path, monkeypatch, fake_pbix):
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    c.post(f"/reports/{name}/extract")

    r = c.post(f"/reports/{name}/table-map", data={"td__Sales": "SELECT * FROM sales_fact"})
    assert r.status_code == 200 and "Mapping saved" in r.text
    saved = (tmp_path / "metrics" / f"{name}.table_map.json").read_text(encoding="utf-8")
    assert "SELECT * FROM sales_fact" in saved

    r = c.post(f"/reports/{name}/scaffold")
    assert r.status_code == 200 and "Template generated" in r.text
    yaml_text = (tmp_path / "metrics" / f"{name}.yaml").read_text(encoding="utf-8")
    assert "FROM (SELECT * FROM sales_fact) AS sales" in yaml_text
