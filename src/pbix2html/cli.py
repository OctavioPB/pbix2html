"""CLI: extract | scaffold | convert | validate | gui."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import extract as ex, semantic
from .config import settings
from .query import FakeBackend, TeradataBackend, run_report
from .render import render_html
from .validate import validate_report, write_markdown


def _kv(items: list[str]) -> dict[str, str]:
    out = {}
    for it in items or []:
        k, _, v = it.partition("=")
        out[k.strip()] = v.strip()
    return out


def _layout_and_model(pbix: Path, out_dir: Path):
    layout = ex.extract_layout(pbix)
    model = ex.extract_model(pbix)
    rdir = out_dir / ex.safe_name(pbix.stem)
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "layout.json").write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")
    (rdir / "model.json").write_text(json.dumps(model, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return layout, model


def _backend(args):
    if args.fake_data:
        block = json.loads(Path(args.fake_data).read_text(encoding="utf-8"))
        return FakeBackend(fixtures={}, default=block)
    if not settings.has_teradata:
        sys.exit("No TERADATA_HOST/USER in .env. Use --fake-data <json> for development.")
    return TeradataBackend()


def cmd_extract(args):
    return ex.main([args.path, "--out", args.out] + (["--no-model"] if args.no_model else []))


def cmd_scaffold(args):
    layout, model = _layout_and_model(Path(args.pbix), Path(args.out))
    table_map, new = semantic.sync_table_map(layout["report"], model)
    if new:
        print(f"table map: {len(new)} table(s) auto-detected from Power Query → "
              f"{semantic.table_map_path(layout['report'])} (review before trusting)")
    path = semantic.write_scaffold(layout, model, overwrite=args.overwrite, table_map=table_map)
    print(f"yaml generated: {path}  (fill in the `sql: TODO` entries)")
    cmd_mapping(args)


def cmd_mapping(args):
    layout, model = _layout_and_model(Path(args.pbix), Path(args.out))
    table_map, _ = semantic.read_table_map(layout["report"])
    rep = semantic.mapping_report(layout, model, table_map)
    path = Path(args.out) / layout["report"] / "mapping_report.md"
    path.write_text(semantic.render_mapping_report(rep), encoding="utf-8")
    v = rep["visuals"]
    print(f"mapping report: {path}  (data visuals {v['data_visuals']}, drafted {v['drafted']})")


def cmd_convert(args):
    pbix = Path(args.pbix)
    layout, model = _layout_and_model(pbix, Path(args.out))
    theme_src = Path(args.theme) if args.theme else semantic.theme_path(layout["report"])
    if theme_src.exists():
        try:
            override = semantic.validate_theme_override(json.loads(theme_src.read_text(encoding="utf-8")))
        except (ValueError, json.JSONDecodeError) as e:
            sys.exit(f"--theme {theme_src}: {e}")
        layout = semantic.apply_theme_override(layout, override)
        print(f"Theme override applied from {theme_src}")
    if not semantic.yaml_path(layout["report"]).exists():
        semantic.write_scaffold(layout, model, table_map=semantic.sync_table_map(layout["report"], model)[0])
        print(f"! metrics/{layout['report']}.yaml didn't exist: generated the scaffold. Fill in the SQL and convert again.")
    spec = semantic.load(layout["report"])
    values = semantic.resolve_params(spec, _kv(args.params))
    role = args.role
    proxy_user = None
    if role:
        r = spec.roles.get(role)
        if r is None:
            sys.exit(f"role {role!r} not defined in the yaml")
        proxy_user = r.get("proxy_user")
    data = None
    if args.mode == "snapshot":
        backend = _backend(args)
        data = run_report(spec, values, backend, proxy_user=proxy_user, use_cache=not args.no_cache)
    html = render_html(layout, spec, values, data, mode=args.mode, role=role, include_hidden=args.include_hidden)
    out = Path(args.html) if args.html else Path(args.out) / (f"{layout['report']}" + (f".{role}" if role else "") + ".html")
    out.write_text(html, encoding="utf-8")
    n_ok = sum(1 for d in (data or {}).values() if d.get("rows")) if data else 0
    n_err = sum(1 for d in (data or {}).values() if d.get("error")) if data else 0
    print(f"HTML: {out}  ({args.mode}; visuals with data: {n_ok}; errors: {n_err})")


def cmd_gui(args):
    try:
        from . import gui
    except ImportError:
        sys.exit("Missing fastapi/uvicorn for the panel. Install with: pip install -e \".[live]\"")
    gui.run(host=args.host, port=args.port, open_browser=not args.no_browser)


def cmd_validate(args):
    spec = semantic.load(args.report)
    values = semantic.resolve_params(spec, _kv(args.params))
    results = validate_report(spec, values, _backend(args))
    path = write_markdown(spec, results)
    print(path.read_text(encoding="utf-8"))
    return 1 if any(r["status"] in ("DIFF", "ERROR") for r in results.values()) else 0


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="pbix2html")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("extract", help=".pbix or folder → out/ (layout, model, inventory)")
    p.add_argument("path")
    p.add_argument("--out", default="out")
    p.add_argument("--no-model", action="store_true")
    p.set_defaults(fn=cmd_extract)

    p = sub.add_parser("scaffold", help="generates an initial metrics/<Report>.yaml")
    p.add_argument("pbix")
    p.add_argument("--out", default="out")
    p.add_argument("--overwrite", action="store_true")
    p.set_defaults(fn=cmd_scaffold)

    p = sub.add_parser("mapping", help="how well the model and visuals map to SQL (out/<Report>/mapping_report.md)")
    p.add_argument("pbix")
    p.add_argument("--out", default="out")
    p.set_defaults(fn=cmd_mapping)

    p = sub.add_parser("convert", help=".pbix → Report.html")
    p.add_argument("pbix")
    p.add_argument("--out", default="out")
    p.add_argument("--html", help="explicit output path")
    p.add_argument("--mode", choices=["snapshot", "live"], default="snapshot")
    p.add_argument("--role")
    p.add_argument("--params", nargs="*", help="k=v (yaml parameters)")
    p.add_argument("--fake-data", help="json {columns,rows} for development without Teradata")
    p.add_argument("--theme", help="path to a theme JSON overriding the extracted one "
                                    "(default: metrics/<report>.theme.json if it exists)")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--include-hidden", action="store_true")
    p.set_defaults(fn=cmd_convert)

    p = sub.add_parser("validate", help="compares sql vs reference_sql/CSV")
    p.add_argument("report")
    p.add_argument("--params", nargs="*")
    p.add_argument("--fake-data")
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("gui", help="local web panel (extract/scaffold/convert/validate without a terminal)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true", help="don't open the browser automatically")
    p.set_defaults(fn=cmd_gui)

    args = ap.parse_args(argv)
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main())
