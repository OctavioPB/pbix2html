"""The `pbix2html` CLI entry points (cli.py), previously untested directly."""
import shutil

from pbix2html import cli, semantic


def test_scaffold_writes_the_mapping_report_for_a_report_name_with_a_space(tmp_path, monkeypatch, fake_pbix):
    """Regression: `_layout_and_model` writes layout.json/model.json under `ex.safe_name(name)`
    (spaces stripped), but `cmd_mapping` used to build its own output path from the raw,
    unsanitized report name — identical for a name like "Executive_Dashboard" (safe_name is a
    no-op), but a mismatched, never-created folder for any name safe_name actually changes,
    e.g. a plain space. `scaffold` calls `cmd_mapping` internally, so this crashed scaffold
    too, not just the standalone `mapping` command."""
    monkeypatch.setattr(semantic, "METRICS_DIR", tmp_path / "metrics")
    monkeypatch.chdir(tmp_path)
    pbix = tmp_path / "Sales Report.pbix"
    shutil.copy(fake_pbix, pbix)

    rc = cli.main(["scaffold", str(pbix), "--out", str(tmp_path / "out")])
    assert rc == 0

    assert (tmp_path / "metrics" / "Sales Report.yaml").exists()
    assert (tmp_path / "out" / "Sales_Report" / "mapping_report.md").exists()


def test_mapping_command_alone_also_lands_under_the_sanitized_folder(tmp_path, monkeypatch, fake_pbix):
    monkeypatch.setattr(semantic, "METRICS_DIR", tmp_path / "metrics")
    monkeypatch.chdir(tmp_path)
    pbix = tmp_path / "Sales Report.pbix"
    shutil.copy(fake_pbix, pbix)

    rc = cli.main(["mapping", str(pbix), "--out", str(tmp_path / "out")])
    assert rc == 0
    assert (tmp_path / "out" / "Sales_Report" / "mapping_report.md").exists()
