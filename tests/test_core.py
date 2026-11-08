"""Business regressions using tiny real DOCX ZIPs and a controlled formula adapter.

These tests exercise ownership, review gates and persisted artifacts, independent
of formula-engine coverage (covered by test_formulas.py).
"""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from xml.sax.saxutils import escape
from zipfile import ZipFile

import pytest

from qa_parser.cli import main
from qa_parser.core import export_document, load_task, parse_document, validate_task


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"


@pytest.fixture(autouse=True)
def deterministic_formula_adapter(monkeypatch):
    def convert(data):
        if b"unknown" in data:
            return {"latex": None, "status": "review", "reason": "unsupported test formula", "engine": "test"}
        return {"latex": r"\frac{U}{I}", "status": "converted", "reason": "", "engine": "test"}
    monkeypatch.setitem(sys.modules, "qa_parser.formulas", SimpleNamespace(convert_omml=convert, convert_mathtype=convert))


def p(text):
    return f'<w:p><w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


def write_docx(path: Path, body: str, images=None):
    with ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f'<w:document xmlns:w="{W}" xmlns:m="{M}" xmlns:r="{R}" xmlns:a="{A}"><w:body>{body}</w:body></w:document>')
        if images:
            rels = []
            for index, (name, data) in enumerate(images.items(), 1):
                z.writestr(f"word/media/{name}", data)
                rels.append(f'<Relationship Id="rId{index}" Target="media/{name}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"/>')
            z.writestr("word/_rels/document.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(rels) + '</Relationships>')
    return path


def choice(number="1", stem="选择正确答案", answer="B"):
    return p(f"{number}．{stem}") + p("A．甲 B．乙 C．丙 D．丁") + p(f"【答案】{answer}") + p("【分析】分析原文") + p("【详解】解析原文")


def test_choice_field_ownership_and_raw_source(tmp_path):
    source = write_docx(tmp_path / "source.docx", p("一、单选题") + choice() + choice("2", "第二题"))
    report = parse_document(source, "choice", tmp_path / "task")
    assert report["total"] == report["passed"] == 2
    task, root = load_task(tmp_path / "task")
    q = task["questions"][0]
    assert q["stem"] == "选择正确答案"
    assert q["options"] == {"A": "甲", "B": "乙", "C": "丙", "D": "丁"}
    assert q["answer"] == "B"
    assert q["analysis"] == "【分析】分析原文\n【详解】解析原文"
    assert "【答案】B" in q["raw_text"]
    assert q["block_indices"] and q["location"].startswith("word/document.xml:")
    assert (root / task["source"]["snapshot"]["path"]).read_bytes() == source.read_bytes()
    assert any("source_xml" in b for b in task["blocks"])
    assert export_document(root, tmp_path / "export")["exported_questions"] == 2


def test_blank_keeps_multi_question_and_answers_in_one_row(tmp_path):
    body = p("一、填空题") + p("1．公共题干") + p("(1)第一空____") + p("(2)第二空____") + p("【答案】(1)铁") + p("(2)铜")
    source = write_docx(tmp_path / "blank.docx", body)
    report = parse_document(source, "blank", tmp_path / "task")
    q = report["questions"][0]
    assert report["total"] == report["passed"] == 1
    assert q["stem"] == "公共题干\n(1)第一空____\n(2)第二空____"
    assert q["answer"] == "(1)铁\n(2)铜"
    assert q["knowledge_point"] == ""


def test_table_options_and_converted_formula_keep_ownership(tmp_path):
    cells = ''.join('<w:tc>' + p(f'{label}．{text}') + '</w:tc>' for label, text in zip('ABCD', ['甲', '乙', '丙', '丁']))
    formula = '<w:p><w:r><w:t>1．求</w:t></w:r><m:oMath><m:r><m:t>ratio</m:t></m:r></m:oMath><w:r><w:t>的值</w:t></w:r></w:p>'
    source = write_docx(tmp_path / "table.docx", formula + '<w:tbl><w:tr>' + cells + '</w:tr></w:tbl>' + p("【答案】C"))
    report = parse_document(source, "choice", tmp_path / "task")
    assert report["passed"] == 1
    question = report["questions"][0]
    assert question["stem"] == r"求$$\frac{U}{I}$$的值"
    assert question["options"] == {"A": "甲", "B": "乙", "C": "丙", "D": "丁"}
    assert question["answer"] == "C"
    task, _ = load_task(tmp_path / "task")
    assert any(block["kind"] == "table_row" and len(block["cells"]) == 4 for block in task["blocks"])


def test_all_duplicate_numbers_need_review(tmp_path):
    source = write_docx(tmp_path / "duplicate.docx", choice("1") + choice("1", "另一个题"))
    report = parse_document(source, "choice", tmp_path / "task")
    assert report["total"] == 2
    assert report["passed"] == 0
    assert all(any(i["code"] == "duplicate_number" for i in q["issues"]) for q in report["questions"])


def test_formula_review_evidence_blocks_export(tmp_path):
    body = '<w:p><w:r><w:t>1．电阻为</w:t></w:r><m:oMath><m:r><m:t>unknown</m:t></m:r></m:oMath></w:p>' + p("A．甲 B．乙 C．丙 D．丁") + p("【答案】A")
    source = write_docx(tmp_path / "formula.docx", body)
    report = parse_document(source, "choice", tmp_path / "task")
    assert report["review"] == 1
    q = report["questions"][0]
    assert any(i["code"] == "formula_review" for i in q["issues"])
    assert "【公式无法可靠保留 F000001】" in q["stem"]
    task, root = load_task(tmp_path / "task")
    assert b"unknown" in (root / task["formulas"][0]["source"]["path"]).read_bytes()
    with pytest.raises(ValueError):
        export_document(root, tmp_path / "export")
    assert not (tmp_path / "export").exists()


def test_formula_state_edit_cannot_silently_approve_placeholder(tmp_path):
    body = '<w:p><w:r><w:t>1．公式</w:t></w:r><m:oMath><m:r><m:t>unknown</m:t></m:r></m:oMath></w:p>' + p("【答案】答案")
    source = write_docx(tmp_path / "formula.docx", body)
    parse_document(source, "blank", tmp_path / "task")
    task, root = load_task(tmp_path / "task")
    task["formulas"][0].update(status="converted", latex="x")
    with pytest.raises(ValueError, match="(?i)(changed|integrity|formula|state)"):
        validate_task(task, root)


def test_images_preserve_position_and_damage_prevents_export(tmp_path):
    image = '<w:r><w:drawing><a:blip r:embed="rId1"/></w:drawing></w:r>'
    body = p("1．装置选择") + '<w:p><w:r><w:t>A．装置甲</w:t></w:r>' + image + '<w:r><w:t>B．乙 C．丙 D．丁</w:t></w:r></w:p>' + p("【答案】A")
    source = write_docx(tmp_path / "image.docx", body, {"first.png": b"\x89PNG\r\n\x1a\nfixture"})
    report = parse_document(source, "choice", tmp_path / "task")
    q = report["questions"][0]
    assert q["options"]["A"] == "装置甲参考aaa0001.png)"
    assert q["asset_filenames"] == ["aaa0001.png"]
    task, root = load_task(tmp_path / "task")
    (root / task["assets"][0]["path"]).write_bytes(b"changed")
    with pytest.raises(ValueError):
        export_document(root, tmp_path / "export", allow_partial=True)
    assert not (tmp_path / "export").exists()


def test_difficulty_independent_of_question_number_and_position(tmp_path):
    first = write_docx(tmp_path / "one.docx", choice("1", "相同题干"))
    second = write_docx(tmp_path / "two.docx", choice("1", "插入题") + choice("2", "相同题干"))
    one = parse_document(first, "choice", tmp_path / "one")
    two = parse_document(second, "choice", tmp_path / "two")
    repeat = parse_document(first, "choice", tmp_path / "repeat")
    assert one["questions"][0]["difficulty"] == two["questions"][1]["difficulty"] == repeat["questions"][0]["difficulty"]


def test_cli_partial_exit_code_and_omission_report(tmp_path, capsys):
    source = write_docx(tmp_path / "partial.docx", choice("1") + choice("2", answer="AB"))
    assert main(["parse", str(source), "--kind", "choice", "--out", str(tmp_path / "task")]) == 2
    assert main(["export", str(tmp_path / "task"), "--out", str(tmp_path / "rejected")]) == 1
    assert not (tmp_path / "rejected").exists()
    assert main(["export", str(tmp_path / "task"), "--out", str(tmp_path / "export"), "--allow-partial"]) == 2
    report = json.loads((tmp_path / "export/report.json").read_text())
    assert report["exported_questions"] == 1
    assert [q["number"] for q in report["omitted_questions"]] == ["2"]


def test_local_unsupported_does_not_prevent_explicit_partial(tmp_path):
    bad = p("2．含未知符号") + '<w:p><w:r><w:sym w:font="Symbol" w:char="F0E0"/></w:r></w:p>' + p("A．甲 B．乙 C．丙 D．丁") + p("【答案】A")
    source = write_docx(tmp_path / "local.docx", choice("1") + bad)
    report = parse_document(source, "choice", tmp_path / "task")
    assert report["passed"] == 1 and report["review"] == 1
    result = export_document(tmp_path / "task", tmp_path / "export", allow_partial=True)
    assert result["exported_questions"] == 1
    assert result["omitted_questions"][0]["number"] == "2"


def test_source_change_and_question_edits_detected(tmp_path):
    source = write_docx(tmp_path / "source.docx", choice())
    parse_document(source, "choice", tmp_path / "task")
    task, root = load_task(tmp_path / "task")
    source.write_bytes(b"changed")
    report = validate_task(task, root, source)
    assert any(i["code"] == "source_changed" for i in report["document_issues"])
    task["questions"][0]["answer"] = "D"
    with pytest.raises(ValueError, match="changed"):
        validate_task(task, root)


def test_explanation_steps_do_not_split_fullwidth_question_numbers(tmp_path):
    body = choice("1") + p("1.先计算电流") + p("2.再计算电阻") + choice("2", "下一道题")
    source = write_docx(tmp_path / "steps.docx", body)
    report = parse_document(source, "choice", tmp_path / "task")
    assert report["total"] == report["passed"] == 2
    assert [q["number"] for q in report["questions"]] == ["1", "2"]
    assert "1.先计算电流\n2.再计算电阻" in report["questions"][0]["analysis"]
    assert report["questions"][1]["stem"] == "下一道题"


def test_stem_letter_reference_is_not_an_option_boundary(tmp_path):
    body = p("1．观察电路 A、判断哪项正确") + ''.join(p(f"{k}．{v}") for k, v in zip("ABCD", ["甲", "乙", "丙", "丁"])) + p("【答案】C")
    source = write_docx(tmp_path / "stem.docx", body)
    report = parse_document(source, "choice", tmp_path / "task")
    assert report["total"] == report["passed"] == 1
    assert report["questions"][0]["stem"] == "观察电路 A、判断哪项正确"
    assert report["questions"][0]["options"] == {"A": "甲", "B": "乙", "C": "丙", "D": "丁"}


def test_source_marker_collision_requires_review(tmp_path):
    source = write_docx(tmp_path / "marker.docx", choice("1", "字面内容⟦I999999⟧"))
    report = parse_document(source, "choice", tmp_path / "task")
    assert report["review"] == 1 and report["passed"] == 0
    assert any("marker" in i["message"] for i in report["questions"][0]["issues"])
    task, _ = load_task(tmp_path / "task")
    assert any("⟦I999999⟧" in block["source_xml"] for block in task["blocks"])
    with pytest.raises(ValueError):
        export_document(tmp_path / "task", tmp_path / "export")
