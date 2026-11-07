from pathlib import Path
from zipfile import ZipFile

from qa_parser.core import run_document


def test_malformed_docx_delivers_failure_report_and_original_bytes(tmp_path):
    source = tmp_path / "broken.docx"
    source.write_bytes(b"not a zip archive")
    result = run_document(source, "choice", tmp_path / "result")
    assert result["status"] == "failed"
    assert result["total"] is None and result["passed"] == 0
    assert (tmp_path / "result" / result["source_evidence"]["path"]).read_bytes() == source.read_bytes()
    assert (tmp_path / "result/quarantine.json").is_file()
    assert not (tmp_path / "result/export/questions.xlsx").exists()


def test_excel_length_error_isolates_one_question_not_whole_run(tmp_path):
    source = tmp_path / "long.docx"
    def p(value):
        return f"<w:p><w:r><w:t>{value}</w:t></w:r></w:p>"
    body = p("1．" + "长" * 33000) + p("A．甲 B．乙 C．丙 D．丁") + p("【答案】A")
    body += p("2．正常题目") + p("A．甲 B．乙 C．丙 D．丁") + p("【答案】B")
    with ZipFile(source, "w") as z:
        z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body + '</w:body></w:document>')
    result = run_document(source, "choice", tmp_path / "result")
    assert result["status"] == "completed_with_quarantine"
    assert result["passed"] == 1 and result["quarantined"] == 1
    assert (tmp_path / "result/export/questions.xlsx").is_file()
