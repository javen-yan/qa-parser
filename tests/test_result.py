import json

from qa_parser.result import user_result, result_text
from qa_parser.cli import main


def test_partial_result_is_explicit_and_diagnostics_are_not_customer_copy():
    q = {"questions": [{"number": "7", "issues": [{"code": "formula_review", "message": "WMF Symbol charset=161 SHA256 deadbeef"}]}], "document_issues": []}
    r = user_result({"status": "completed_with_quarantine", "total": 10, "passed": 9}, q)
    text = result_text(r)
    assert r["status"] == "partial_failure"
    assert "部分失败" in text and "结果不完整" in text and "第7题" in text
    assert "WMF" not in text and "SHA256" not in text


def test_zero_pass_is_failure_even_if_processing_completed():
    r = user_result({"status": "completed_with_quarantine", "total": 3, "passed": 0}, {"questions": []})
    assert r["status"] == "failure" and r["export"] is None


def test_all_pass_is_success():
    r = user_result({"status": "completed", "total": 3, "passed": 3}, {"questions": []})
    assert r["status"] == "success" and "全部 3 题" in r["message"]


def test_cli_readable_failure_preserves_machine_diagnostics(tmp_path, capsys):
    source = tmp_path / "broken.docx"
    source.write_bytes(b"bad zip")
    out = tmp_path / "run"
    assert main(["run", str(source), "--kind", "choice", "--out", str(out)]) == 1
    message = capsys.readouterr().out
    assert "转换失败" in message and "result.txt" in message
    assert "ValueError" not in message and "Traceback" not in message
    assert "run_failure" in (out / "quarantine.json").read_text()
    assert json.loads((out / "summary.json").read_text())["user_result"]["status"] == "failure"


def test_output_conflict_is_clear_without_overwriting(tmp_path, capsys):
    out = tmp_path / "existing"
    out.mkdir()
    (out / "keep").write_text("unchanged")
    assert main(["run", str(tmp_path / "absent.docx"), "--kind", "choice", "--out", str(out)]) == 1
    message = capsys.readouterr().err
    assert "输出位置已存在" in message and "诊断报告" in message
    assert (out / "keep").read_text() == "unchanged"
