"""Automatic image fallback/quarantine contracts using real miniature DOCX files."""
import hashlib
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from xml.sax.saxutils import escape
from zipfile import ZipFile

from PIL import Image, ImageDraw

import pytest

from qa_parser import core


def visible_formula_png():
    image = Image.new("RGB", (120, 40), "white")
    draw = ImageDraw.Draw(image)
    draw.text((8, 12), "R = U / I", fill="black")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


PNG = visible_formula_png()
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
V = "urn:schemas-microsoft-com:vml"
O = "urn:schemas-microsoft-com:office:office"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"


@pytest.fixture(autouse=True)
def failed_formula_adapter(monkeypatch):
    def convert(data):
        return {"latex": None, "status": "review", "reason": "unsupported fixture structure", "engine": "fixture"}
    monkeypatch.setitem(sys.modules, "qa_parser.formulas", SimpleNamespace(convert_omml=convert, convert_mathtype=convert))


def text(value):
    return '<w:r><w:t xml:space="preserve">' + escape(value) + '</w:t></w:r>'


def paragraph(value):
    return '<w:p>' + text(value) + '</w:p>'


def ole():
    return '<w:r><w:object><v:shape><v:imagedata r:id="preview"/></v:shape><o:OLEObject Type="Embed" ProgID="Equation.DSMT4" r:id="ole"/></w:object></w:r>'


def options():
    return paragraph("A．甲 B．乙 C．丙 D．丁") + paragraph("【答案】A")


def valid_question(number="2"):
    return paragraph(f"{number}．纯文字有效题") + options()


def docx(path, body):
    with ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f'<w:document xmlns:w="{W}" xmlns:m="{M}" xmlns:r="{R}" xmlns:v="{V}" xmlns:o="{O}" xmlns:a="{A}"><w:body>{body}</w:body></w:document>')
        z.writestr("word/_rels/document.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="preview" Target="media/preview.png"/><Relationship Id="ole" Target="embeddings/formula.bin"/><Relationship Id="picture" Target="media/ordinary.png"/></Relationships>')
        z.writestr("word/media/preview.png", PNG)
        z.writestr("word/media/ordinary.png", PNG)
        z.writestr("word/embeddings/formula.bin", b"fixture OLE supplied to mocked adapter")
    return path


def run(source, output):
    return core.run_document(source, "choice", output)


def task(output):
    return json.loads((output / "task/task.json").read_text(encoding="utf-8"))


def test_failed_formula_uses_original_preview_in_original_order(tmp_path):
    body = '<w:p>' + text('1．前') + ole() + text('中') + '<w:r><w:drawing><a:blip r:embed="picture"/></w:drawing></w:r>' + text('后') + ole() + text('末') + '</w:p>' + options()
    source = docx(tmp_path / "source.docx", body)
    output = tmp_path / "run"
    run(source, output)
    parsed = task(output)
    report = json.loads((output / "task/report.json").read_text())
    assert report["passed"] == 1 and report["review"] == 0
    assert report["formulas"]["image"] == 2
    q = report["questions"][0]
    formula = parsed["formulas"]
    assert all(f["status"] == "image" for f in formula)
    assets = {a["id"]: a for a in parsed["assets"]}
    names = [assets[f["fallback_asset_id"]]["filename"] for f in formula]
    ordinary = next(a["filename"] for a in parsed["assets"] if a["id"] not in {f["fallback_asset_id"] for f in formula})
    assert q["stem"] == f"前请参考 {names[0]}中请参考 {ordinary}后请参考 {names[1]}末"
    assert q["asset_filenames"] == [names[0], ordinary, names[1]]
    for f in formula:
        a = assets[f["fallback_asset_id"]]
        assert a["source_preview_sha256"] == f["preview"]["sha256"] == hashlib.sha256(PNG).hexdigest()
        assert (output / "task" / f["preview"]["path"]).read_bytes() == PNG
        assert hashlib.sha256((output / "task" / a["path"]).read_bytes()).hexdigest() == a["sha256"]
        assert (output / "export/images" / a["filename"]).read_bytes() == (output / "task" / a["path"]).read_bytes()
    assert (output / "export/questions.xlsx").is_file()


def test_failed_omml_without_preview_is_quarantined_not_guessed(tmp_path):
    omml = '<m:oMath><m:r><m:t>unsupported</m:t></m:r></m:oMath>'
    source = docx(tmp_path / "source.docx", '<w:p>' + text('1．未知公式') + omml + '</w:p>' + options() + valid_question())
    output = tmp_path / "run"
    run(source, output)
    parsed = task(output)
    assert parsed["formulas"][0]["status"] == "review"
    assert not parsed["formulas"][0].get("fallback_asset_id")
    report = json.loads((output / "export/report.json").read_text())
    assert report["exported_questions"] == 1
    assert report["rows"][0]["number"] == "2"
    assert report["omitted_questions"][0]["number"] == "1"
    assert (output / "quarantine.json").is_file()
    assert (output / "summary.json").is_file()


def test_bad_option_structure_is_automatically_isolated(tmp_path):
    source = docx(tmp_path / "source.docx", paragraph('1．缺失选择项') + paragraph('A．甲 C．丙') + paragraph('【答案】A') + valid_question())
    output = tmp_path / "run"
    run(source, output)
    report = json.loads((output / "export/report.json").read_text())
    assert report["exported_questions"] == 1
    omitted = report["omitted_questions"][0]
    assert omitted["number"] == "1"
    assert any(i["code"] == "option_structure" for i in omitted["issues"])
    assert (output / "quarantine.json").is_file()


def test_reliable_formula_image_does_not_override_structural_error(tmp_path):
    source = docx(tmp_path / "source.docx", '<w:p>' + text('1．有原图但缺选项') + ole() + '</w:p>' + paragraph('A．甲 C．丙') + paragraph('【答案】A') + valid_question())
    output = tmp_path / "run"
    summary = run(source, output)
    parsed = task(output)
    assert parsed["formulas"][0]["status"] == "image"
    assert summary["quarantined"] == 1 and summary["passed"] == 1
    assert summary["exported_formulas"]["image"] == 0
    report = json.loads((output / "export/report.json").read_text())
    assert [row["number"] for row in report["rows"]] == ["2"]
    assert any(i["code"] == "option_structure" for i in report["omitted_questions"][0]["issues"])
    assert not list((output / "export/images").iterdir())


def test_all_quarantined_still_finishes_with_evidence_and_no_empty_workbook(tmp_path):
    source = docx(tmp_path / "source.docx", paragraph('1．没有答案也没有选项'))
    output = tmp_path / "run"
    run(source, output)
    assert (output / "summary.json").is_file()
    assert (output / "quarantine.json").is_file()
    assert (output / "task/source/input.docx").read_bytes() == source.read_bytes()
    assert not (output / "export/questions.xlsx").exists()


def test_automatic_run_does_not_overwrite_and_fallback_is_repeatable(tmp_path):
    source = docx(tmp_path / "source.docx", '<w:p>' + text('1．公式') + ole() + '</w:p>' + options())
    first, second = tmp_path / "first", tmp_path / "second"
    run(source, first)
    run(source, second)
    a, b = task(first), task(second)
    assert a["questions"] == b["questions"]
    assert a["formulas"] == b["formulas"]
    assert a["assets"] == b["assets"]
    with pytest.raises((ValueError, FileExistsError)):
        run(source, first)
