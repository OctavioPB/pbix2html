"""
Numeric validation: `sql` vs `reference_sql` (SQL captured from DBQL) or a reference CSV
at tests/reference/<Report>/<visual>.csv. Produces out/<Report>.validation.md.
"""
from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any

from .query import Backend, DataBlock, bind
from .semantic import ReportSpec

REFERENCE_DIR = Path("tests/reference")


def _index(block: DataBlock) -> tuple[list[str], dict[tuple, dict[str, Any]]]:
    """Indexes rows by non-numeric columns (categories)."""
    cols = block["columns"]
    num = [i for i, c in enumerate(cols) if any(isinstance(r[i], (int, float)) and not isinstance(r[i], bool) for r in block["rows"])]
    key_idx = [i for i in range(len(cols)) if i not in num]
    out = {}
    for r in block["rows"]:
        out[tuple(str(r[i]) for i in key_idx)] = {cols[i]: r[i] for i in num}
    return [cols[i] for i in num], out


def _close(a: Any, b: Any, tol: dict[str, float]) -> bool:
    if a is None or b is None:
        return a == b
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return a == b
    if "abs" in tol and abs(a - b) <= tol["abs"]:
        return True
    rel = tol.get("rel", 1e-6)
    return math.isclose(a, b, rel_tol=rel, abs_tol=0.0)


def compare(actual: DataBlock, expected: DataBlock, tol: dict[str, float]) -> list[str]:
    diffs: list[str] = []
    ncols_a, idx_a = _index(actual)
    ncols_e, idx_e = _index(expected)
    if len(ncols_a) != len(ncols_e):
        diffs.append(f"different numeric columns: {ncols_a} vs {ncols_e}")
    for key in sorted(set(idx_a) | set(idx_e)):
        ra, re_ = idx_a.get(key), idx_e.get(key)
        if ra is None:
            diffs.append(f"falta en actual: {key}")
        elif re_ is None:
            diffs.append(f"sobra en actual: {key}")
        else:
            for ca, ce in zip(ncols_a, ncols_e):
                if not _close(ra[ca], re_[ce], tol):
                    diffs.append(f"{key or '(total)'} {ca}: {ra[ca]} vs {re_[ce]}")
    return diffs


def _load_csv(path: Path) -> DataBlock:
    with path.open(encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    cols = [c.strip().lower() for c in rows[0]]
    def conv(x: str):
        try:
            return float(x.replace(",", ""))
        except ValueError:
            return x
    return {"columns": cols, "rows": [[conv(x) for x in r] for r in rows[1:]]}


def validate_report(spec: ReportSpec, values: dict[str, Any], backend: Backend, proxy_user: str | None = None) -> dict[str, dict]:
    results: dict[str, dict] = {}
    for vid, v in spec.visuals.items():
        if not v.has_data:
            continue
        sql, bound = bind(v.sql, v.params, values)
        try:
            actual = backend.execute(sql, bound, proxy_user)
        except Exception as e:
            results[vid] = {"status": "ERROR", "detail": [f"{type(e).__name__}: {e}"]}
            continue
        expected = None
        csv_ref = REFERENCE_DIR / spec.report / f"{vid}.csv"
        if v.reference_sql:
            rsql, rbound = bind(v.reference_sql, v.params, values)
            expected = backend.execute(rsql, rbound, proxy_user)
        elif csv_ref.exists():
            expected = _load_csv(csv_ref)
        if expected is None:
            results[vid] = {"status": "SKIP", "detail": ["sin reference_sql ni CSV de referencia"]}
            continue
        diffs = compare(actual, expected, v.tolerance)
        results[vid] = {"status": "OK" if not diffs else "DIFF", "detail": diffs[:50]}
    return results


def write_markdown(spec: ReportSpec, results: dict[str, dict], out_dir: Path = Path("out")) -> Path:
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"{spec.report}.validation.md"
    lines = [f"# Validation — {spec.report}\n", "| Visual | Kind | Status | Detail |", "|---|---|---|---|"]
    for vid, r in results.items():
        v = spec.visuals[vid]
        detail = "<br>".join(r["detail"][:5]) + (" …" if len(r["detail"]) > 5 else "")
        lines.append(f"| {vid} ({v.title or ''}) | {v.kind} | **{r['status']}** | {detail} |")
    counts = {s: sum(1 for r in results.values() if r["status"] == s) for s in ("OK", "DIFF", "SKIP", "ERROR")}
    lines.append(f"\nOK {counts['OK']} · DIFF {counts['DIFF']} · SKIP {counts['SKIP']} · ERROR {counts['ERROR']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
