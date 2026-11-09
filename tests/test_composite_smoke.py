"""Executable smoke assertions use only freshly generated public fixtures."""
import importlib.util
import json
from pathlib import Path
import sys


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
try:
    spec = importlib.util.spec_from_file_location("composite_smoke", SCRIPTS / "composite_smoke.py")
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
finally:
    sys.path.remove(str(SCRIPTS))


def test_source_composite_smoke_runs_real_cli_and_all_sheets(tmp_path):
    out = tmp_path / "合成 回归"
    assert smoke.main(["--out", str(out)]) == 0
    report = json.loads((out / "composite-report.json").read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert "frozen" not in report["runs"]
    files = report["runs"]["development"]["files"]
    assert sum(f["counts"]["exported"] for f in files) == 9
    assert sum(f["counts"]["policy_skipped"] for f in files) == 1
    assert sum(f["counts"]["failed_or_review"] for f in files) == 1
    assert files[1]["sheets"] == ["选择", "考点"]


def test_workbook_reader_checks_every_sheet_and_sparse_columns(tmp_path):
    import xlsxwriter
    path = tmp_path / "sparse.xlsx"
    with xlsxwriter.Workbook(path) as book:
        a = book.add_worksheet("选择")
        a.write_string(0, 0, "甲")
        a.write_string(0, 26, "乙")
        b = book.add_worksheet("考点")
        b.write_string(0, 1, "不同页")
    sheets = smoke.read_workbook(path)
    assert list(sheets) == ["选择", "考点"]
    assert len(sheets["选择"][0]) == 27
    assert sheets["选择"][0][26] == "乙"
    assert sheets["考点"] == [["", "不同页"]]
