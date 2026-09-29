"""'Enter Data' tables (rows embedded in the M source) and the read-only validator."""
import base64
import json
import zlib

import pytest

from pbix2html import semantic


def _m(rows, types="a = _t, b = _t"):
    raw = json.dumps(rows).encode()
    comp = zlib.compressobj(wbits=-15)
    payload = base64.b64encode(comp.compress(raw) + comp.flush()).decode()
    return ('let\n    Source = Table.FromRows(Json.Document(Binary.Decompress(Binary.FromText("' + payload +
            '", BinaryEncoding.Base64), Compression.Deflate)), let _t = ((type nullable text) meta '
            f'[Serialized.Text = true]) in type table [{types}]),\n    #"Changed Type" = x\nin\n    #"Changed Type"')


def test_inline_table_becomes_union_all():
    sql = semantic._detect_table_query(_m([["x", "it's"], ["y", None]]))
    assert sql == ("SELECT CAST('x' AS VARCHAR(1)) AS a, CAST('it''s' AS VARCHAR(4)) AS b\n"
                   "UNION ALL\nSELECT 'y', NULL")
    assert semantic.validate_read_only_sql(sql)


def test_inline_table_numeric_columns_and_reserved_names():
    sql = semantic._detect_table_query(_m([["2026-01", 5], ["2026-02", 7.5]], "date = _t, n = number"))
    assert sql.startswith("SELECT CAST('2026-01' AS VARCHAR(7)) AS \"date\", CAST(5 AS DECIMAL(18,6)) AS n")
    assert "SELECT '2026-02', 7.5" in sql


@pytest.mark.parametrize("bad", [
    _m([["x"]], "a = _t, b = _t"),                 # ragged row
    _m([["x", "y"]] * 501),                         # too many rows to inline
    "let Source = Table.FromRows(Json.Document(Binary.Decompress(Binary.FromText(\"!!\", "
    "BinaryEncoding.Base64), Compression.Deflate)), type table [a = _t]) in Source",   # not base64
])
def test_inline_table_falls_back_to_none(bad):
    assert semantic._detect_table_query(bad) is None


def test_validator_ignores_keywords_inside_string_literals_only():
    assert semantic.validate_read_only_sql("SELECT 'SET', 'it''s an INSERT' AS x")
    for sql in ("SELECT 1; DROP TABLE t", "SELECT 1 FROM t WHERE a = 'x' OR DELETE FROM t",
                "SELECT 'a' -- don't\nUNION SELECT 1 FROM t INTO x",
                "SELECT 1 /* it's */ ; DELETE FROM t"):
        with pytest.raises(ValueError):
            semantic.validate_read_only_sql(sql)
