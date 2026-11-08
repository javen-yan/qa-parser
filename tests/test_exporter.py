from copy import deepcopy
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from zipfile import ZipFile

import pytest

from qa_parser.exporter import BLANK_COLUMNS, CHOICE_COLUMNS, ExportError, export_task


def sample_task():
    return {
        "schema_version": 1, "kind": "choice", "config": {},
        "source": {"name": "样本.docx", "sha256": "a" * 64},
        "questions": [{"id": "q1", "number": "1", "stem": "=1+1",
                       "options": {"A": "http://example.com", "B": "2", "C": "3", "D": "4"},
                       "answer": "B", "analysis": "$$x^2$$", "difficulty": "低", "issues": []}],
        "assets": [],
    }


def cells(path):
    with ZipFile(path) as z:
        xml = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    return xml, {c.attrib["r"]: "".join(c.itertext()) for c in xml.findall(".//s:c", ns)}


def test_choice_exact_columns_and_literal_strings(tmp_path):
    report = export_task(sample_task(), tmp_path, tmp_path / "导出 空格")
    xml, values = cells(tmp_path / "导出 空格/questions.xlsx")
    assert [values[f"{chr(65+i)}1"] for i in range(14)] == CHOICE_COLUMNS
    assert values["D2"] == "=1+1"
    assert values["I2"] == "http://example.com"
    assert values["E2"] == "A．http://example.com\nB．2\nC．3\nD．4"
    assert values["G2"] == "$$x^2$$"
    assert not any(e.tag.endswith("}f") or e.tag.endswith("}hyperlink") for e in xml.iter())
    assert report["import_contract"] == "unverified"
    assert report["rows"][0]["excel_row"] == 2


def test_blank_preserves_subquestions_and_empty_knowledge(tmp_path):
    task = sample_task()
    task["kind"] = "blank"
    task["questions"][0].update(stem="公共题干\n(1)第一空___\n(2)第二空___", answer="(1)铁\n(2)铜")
    export_task(task, tmp_path, tmp_path / "out")
    _, values = cells(tmp_path / "out/questions.xlsx")
    assert [values[f"{chr(65+i)}1"] for i in range(4)] == BLANK_COLUMNS
    assert values["C2"] == task["questions"][0]["stem"]
    assert values["D2"] == "(1)铁\n(2)铜"
    assert values["B2"] == ""


def test_partial_must_be_explicit_and_reports_omission(tmp_path):
    task = sample_task()
    failed = deepcopy(task["questions"][0])
    failed.update(id="q2", number="2", issues=[{"code": "formula", "message": "Unknown template", "location": "p:12"}])
    task["questions"].append(failed)
    with pytest.raises(ExportError, match="explicitly"):
        export_task(task, tmp_path, tmp_path / "out")
    assert not (tmp_path / "out").exists()
    report = export_task(task, tmp_path, tmp_path / "out", allow_partial=True)
    assert report["exported_questions"] == 1
    assert report["omitted_questions"][0]["number"] == "2"
    assert json.loads((tmp_path / "out/report.json").read_text()) == report


def add_image(task, root: Path, filename="0001.png", data=b"image"):
    (root / "assets").mkdir(exist_ok=True)
    (root / "assets" / filename).write_bytes(data)
    task["assets"].append({"filename": filename, "path": f"assets/{filename}", "sha256": hashlib.sha256(data).hexdigest()})


def test_only_used_images_and_hash_verified(tmp_path):
    task = sample_task()
    add_image(task, tmp_path)
    add_image(task, tmp_path, "0002.jpeg")
    task["questions"][0]["options"]["A"] = "装置，请参考 0001.png"
    export_task(task, tmp_path, tmp_path / "out")
    assert [p.name for p in (tmp_path / "out/images").iterdir()] == ["0001.png"]
    (tmp_path / "assets/0001.png").write_bytes(b"tampered")
    with pytest.raises(ExportError, match="integrity"):
        export_task(task, tmp_path, tmp_path / "out2")
    assert not (tmp_path / "out2").exists()


def test_missing_reference_and_escape_fail(tmp_path):
    task = sample_task()
    task["questions"][0]["stem"] = "请参考 0001.png"
    with pytest.raises(ExportError, match="missing from manifest"):
        export_task(task, tmp_path, tmp_path / "out")
    add_image(task, tmp_path)
    task["assets"][0]["path"] = "../0001.png"
    with pytest.raises(ExportError, match="Unsafe"):
        export_task(task, tmp_path, tmp_path / "out")


def test_long_cell_never_silently_truncates(tmp_path):
    task = sample_task()
    task["questions"][0]["stem"] = "字" * 32768
    with pytest.raises(ExportError, match="cell limit"):
        export_task(task, tmp_path, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_output_not_overwritten(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep").write_text("untouched")
    with pytest.raises(ExportError, match="already exists"):
        export_task(sample_task(), tmp_path, out)
    assert (out / "keep").read_text() == "untouched"


def test_contract_defaults_and_required_fields(tmp_path):
    task = sample_task()
    task["config"] = {"field_defaults": {"questionType": "SINGLE", "ID": "001"}, "required_columns": ["questionType"]}
    export_task(task, tmp_path, tmp_path / "out")
    _, values = cells(tmp_path / "out/questions.xlsx")
    assert values["N2"] == "SINGLE"
    assert values["A2"] == "001"
    task["config"]["field_defaults"] = {}
    with pytest.raises(ExportError, match="Required"):
        export_task(task, tmp_path, tmp_path / "out2")


@pytest.mark.parametrize("field", ["stem", "answer", "analysis", "optionA"])
def test_customer_reference_without_manifest_fails_in_every_field(tmp_path, field):
    task = sample_task()
    if field == 'optionA':
        task['questions'][0]['options']['A'] = '前参考aaa0001.png)后'
    else:
        task['questions'][0][field] = '前参考aaa0001.png)后'
    with pytest.raises(ExportError, match='missing from manifest'):
        export_task(task, tmp_path, tmp_path/'out')
