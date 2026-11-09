#!/usr/bin/env python3
"""Synthetic composite/blank/table acceptance for source and relocated bundles.

No private reference documents are read. The optional executable must be the
relocated executable already unpacked by windows_smoke.py or macos_smoke.py.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import posixpath
import subprocess
import sys
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from windows_smoke import HEADERS, paragraph, write_docx

ROOT = Path(__file__).resolve().parents[1]
S = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def table(rows):
    return "<w:tbl>" + "".join("<w:tr>" + "".join("<w:tc>" + paragraph(c) + "</w:tc>" for c in row) + "</w:tr>" for row in rows) + "</w:tbl>"


def fixtures(directory: Path):
    directory.mkdir(parents=True)
    # Fixed bytes of our synthetic blue ellipse, not a source-document asset.
    # Encoding once keeps cross-platform semantic hashes independent of zlib.
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAACgAAAAeCAIAAADRv8uKAAAAuElEQVR4nO2WQQ6FMAhEO6T3vzJ/YWJMpMBUqib+WZkUeJZCC1S1PSF5hNq+CO6UNeCtUtXSr/POZpk/6BTSj7gbbx+BsQ7Wj0i24zK+MMFU0kK8GUQWUY/uZolI6FbCjsGZuqC0hQJccLJt5gREqS5/NbLFdY/kJs6rwagusbiPdeUwourueNR20xpdDOI7lFBNCXvHTlDNE8S7nsWLg0DKWBPbYXNeMPpQp14/7K1odCmL9Ac3Xz+Kik4vdO0pYwAAAABJRU5ErkJggg==")
    fraction = "<m:oMath><m:f><m:num><m:r><m:t>U</m:t></m:r></m:num><m:den><m:r><m:t>I</m:t></m:r></m:den></m:f></m:oMath>"
    drawing = '<w:r><w:drawing><a:blip r:embed="rId1"/></w:drawing></w:r>'
    shared = paragraph("根据合成材料，完成下面小题。")
    shared += "<w:p><w:r><w:t>共同材料公式</w:t></w:r>" + fraction + drawing + "</w:p>"
    shared += paragraph("1．子题一选择。") + paragraph("A．甲 B．乙 C．丙 D．丁")
    shared += paragraph("2．子题二选择。") + paragraph("A．戊 B．己 C．庚 D．辛")
    shared += paragraph("【答案】1.A    2.D")
    shared += paragraph("【解析】1．子题一分析。") + paragraph("2．子题二分析。")
    first = write_docx(directory / "01.docx", shared, png)
    mixed = paragraph("1．本能行为又称____。") + paragraph("【答案】非条件反射/简单反射")
    mixed += paragraph("2．列举____。") + paragraph("【答案】甲，乙")
    mixed += paragraph("3．前项____，后项____。") + paragraph("【答案】甲     乙")
    mixed += paragraph("4．固定规则名称为____。")
    mixed += table([["项目", "既定值"], ["示例", "7"]]) + paragraph("【答案】常量")
    mixed += paragraph("5．请填写下表。") + table([["类别", "内容"], ["甲类", "①____"]])
    mixed += paragraph("①________") + paragraph("【答案】示例答案")
    mixed += paragraph("6．解释上述实验的方法。") + paragraph("【答案】展示分析方法。")
    mixed += paragraph("7．选择正确项。") + paragraph("A．左 B．右 C．前 D．后") + paragraph("【答案】B")
    mixed += paragraph("8．公共说明。") + paragraph("(1)填入____") + paragraph("(2)填入____")
    mixed += paragraph("【答案】(1)赤") + paragraph("(2)蓝")
    second = write_docx(directory / "02.docx", mixed)
    numbered = paragraph("请按连续编号补全：__76__、__77__、__78__。")
    numbered += paragraph("【答案】76．一    77．二    78．三")
    third = write_docx(directory / "03.docx", numbered)
    return [first, second, third], png


def read_workbook(path: Path) -> dict[str, list[list[str]]]:
    """Read every actual workbook sheet, including relationships and sparse cells."""
    with ZipFile(path) as archive:
        require(archive.testzip() is None, "Workbook CRC failed")
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = ["".join(t.text or "" for t in si.findall(".//s:t", S))
                      for si in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("s:si", S)]
        relationships = {r.attrib["Id"]: r.attrib["Target"]
                         for r in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))}
        sheets = ET.fromstring(archive.read("xl/workbook.xml")).findall("s:sheets/s:sheet", S)
        result = {}
        for sheet in sheets:
            target = relationships[sheet.attrib[f"{{{REL}}}id"]]
            target = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
            require(target.startswith("xl/"), "Worksheet relationship escapes workbook")
            rows = []
            for row in ET.fromstring(archive.read(target)).findall("s:sheetData/s:row", S):
                values = {}
                for cell in row.findall("s:c", S):
                    column = 0
                    for char in cell.attrib["r"]:
                        if char.isalpha():
                            column = column * 26 + ord(char) - 64
                    value = cell.findtext("s:v", "", S)
                    if cell.attrib.get("t") == "s":
                        value = shared[int(value)]
                    elif cell.attrib.get("t") == "inlineStr":
                        value = "".join(t.text or "" for t in cell.findall("s:is//s:t", S))
                    values[column - 1] = value
                rows.append([values.get(i, "") for i in range(max(values, default=-1) + 1)])
            result[sheet.attrib["name"]] = rows
        return result


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _issue_semantics(issues):
    return [{k: i[k] for k in ("code", "location", "policy_skip") if k in i} for i in issues]


def validate_batch(root: Path, png: bytes):
    batch = _json(root / "batch-summary.json")
    require(batch["status"] == "partial_failure", "Unknown/table cases must remain visible in batch status")
    require(batch["passed"] == 9 and batch["quarantined"] == 2, "Expected 9 passed and 2 explicitly omitted synthetic output units")
    require(len(batch["files"]) == 3, "Batch did not finish all three input files")
    semantics = []
    for entry in batch["files"]:
        work = Path(entry["output"])
        task = _json(work / "task/task.json")
        summary = _json(work / "summary.json")
        report = _json(work / "export/report.json")
        require("export_preview_error" not in summary, "Workbook preview failed")
        require(task["schema_version"] == 2, "Composite task schema must be explicit")
        questions = task["questions"]
        qmap = {q["number"]: q for q in questions}
        require(len(qmap) == len(questions), "Duplicate source numbers hidden in smoke fixture")
        workbook = read_workbook(work / "export/questions.xlsx")
        for sheet in report["sheets"]:
            require(workbook[sheet["name"]][0] == HEADERS[sheet["kind"]], "Existing sheet column contract changed")
            require(len(workbook[sheet["name"]]) == sheet["rows"] + 1, "Workbook row count differs from report")
        for mapping in report["rows"]:
            q = qmap[mapping["number"]]
            rows = workbook[mapping["sheet"]]
            cell = dict(zip(rows[0], rows[mapping["excel_row"] - 1]))
            require(cell["题目"] == q["stem"] and cell["答案"] == q["answer"], "Export field ownership differs from task")
            require(mapping["id"] == q["id"] and mapping["parent_id"] == q.get("parent_id"), "Export row lost parent/child binding")
            if q["kind"] == "blank":
                require(cell["考点"] == q["knowledge_point"] and bool(cell["考点"]), "Blank completed statement missing")
        counts = summary["counts"]
        if entry["name"] == "01.docx":
            require(set(qmap) == {"1", "2"}, "Shared group was not split into its two children")
            require(counts == {"original_questions": 1, "subquestions": 2, "output_units": 2,
                               "exported": 2, "policy_skipped": 0, "failed_or_review": 0}, "Shared group counts are wrong")
            require(qmap["1"]["parent_id"] and qmap["1"]["parent_id"] == qmap["2"]["parent_id"], "Shared parent missing")
            for number, answer in (("1", "A"), ("2", "D")):
                q = qmap[number]
                require(q["answer"] == answer, "Concentrated answer assigned to wrong child")
                require(r"$$\frac{U}{I}$$" in q["stem"] and "参考aaa001.png)" in q["stem"], "Child lost shared formula/image")
                require(bool(q["material_refs"]) and bool(q["source_spans"]), "Material source provenance missing")
            require("子题一分析。" in qmap["1"]["analysis"] and "子题二分析。" not in qmap["1"]["analysis"], "Analysis crossed child boundaries")
            require("子题二分析。" in qmap["2"]["analysis"] and "子题一分析。" not in qmap["2"]["analysis"], "Analysis crossed child boundaries")
            require(report["image_reference_map"] == {"参考aaa001.png)": "001.png"}, "Image alias contract changed")
            require((work / "export/images/001.png").read_bytes() == png, "Original shared PNG changed")
        elif entry["name"] == "02.docx":
            require(set(workbook) == {"选择", "考点"}, "Mixed document must use both established templates")
            require(counts == {"original_questions": 8, "subquestions": 0, "output_units": 8,
                               "exported": 6, "policy_skipped": 1, "failed_or_review": 1}, "Mixed-document counts are wrong")
            for number, answer, knowledge in (
                ("1", "非条件反射/简单反射", "本能行为又称非条件反射/简单反射。"),
                ("2", "甲，乙", "列举甲，乙。"),
                ("3", "甲,乙", "前项甲，后项乙。"),
            ):
                require(qmap[number]["answer"] == answer and qmap[number]["knowledge_point"] == knowledge,
                        "Blank punctuation, synonym binding or completed statement changed")
                require("____" in qmap[number]["stem"], "Exported stem lost original blank")
            require(qmap["4"]["table_roles"][0]["role"] == "static_material", "Static table incorrectly skipped")
            require("示例\t7" in qmap["4"]["stem"] and "固定规则名称为常量。" in qmap["4"]["knowledge_point"], "Static material lost in export")
            omitted = {q["number"]: q for q in report["omitted_questions"]}
            require(set(omitted) == {"5", "6"}, "Unexpected omissions or silent unsupported export")
            require(omitted["5"]["policy_skipped"] and not omitted["6"]["policy_skipped"], "Policy skip and failure not distinguished")
            require(any(i["code"] == "table_interaction_skipped" for i in omitted["5"]["issues"]), "Interactive table lacks policy evidence")
            require(any(i["code"] == "unsupported_kind" for i in omitted["6"]["issues"]), "Essay lacks unsupported-type reason")
            require(qmap["8"]["answer"] == "赤,蓝" and "(1)填入赤" in qmap["8"]["knowledge_point"]
                    and "(2)填入蓝" in qmap["8"]["knowledge_point"], "Numbered subquestion bindings lost")
        elif entry["name"] == "03.docx":
            require(len(questions) == 1 and questions[0]["organization"] == "numbered_blanks", "Numbered blanks split into top-level questions")
            q = questions[0]
            require(q["answer"] == "一,二,三" and q["knowledge_point"] == "请按连续编号补全：一、二、三。", "Numbered blank binding failed")
            require(counts["original_questions"] == counts["output_units"] == counts["exported"] == 1, "Numbered blank count is not one question")
        else:
            raise AssertionError("Unexpected smoke input")
        images = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((work / "export/images").iterdir())}
        semantics.append({"name": entry["name"], "sheets": workbook, "counts": counts,
                          "questions": [{**{k: q.get(k) for k in ("id", "number", "kind", "parent_id", "number_path", "organization",
                              "material_refs", "source_spans", "stem", "answer", "analysis", "options", "knowledge_point", "difficulty",
                              "asset_filenames", "formula_ids", "blank_slots", "answer_bindings", "table_roles", "table_slot_aliases")},
                              "issues": _issue_semantics(q.get("issues", []))} for q in questions],
                          "omitted": [{"number": q["number"], "policy_skipped": q["policy_skipped"], "issues": _issue_semantics(q["issues"])}
                                      for q in report["omitted_questions"]], "images": images,
                          "image_reference_map": report["image_reference_map"], "row_map": report["rows"]})
    with ZipFile(batch["delivery"]) as archive:
        require(archive.testzip() is None, "Delivery ZIP CRC failed")
        require({"01.xlsx", "01/001.png", "02.xlsx", "03.xlsx"} <= set(archive.namelist()), "Numeric delivery names changed")
    return semantics


def execute(command, inputs, out, env, log):
    child = dict(env, QA_PARSER_CLI_LOG=str(log))
    with log.open("w", encoding="utf-8") as stream:
        result = subprocess.run(command + ["batch", *map(str, inputs), "--mode", "auto", "--out", str(out)],
                                cwd=log.parent, env=child, stdout=stream, stderr=subprocess.STDOUT, timeout=240)
    require(result.returncode == 2, f"Batch exit {result.returncode} != explicit partial 2: {log.read_text(encoding='utf-8', errors='replace')[-2000:]}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    inputs, png = fixtures(out / "中文 合成输入")
    modes = {"development": [sys.executable, "-m", "qa_parser"]}
    if args.exe:
        require(args.exe.is_file(), "Relocated frozen executable missing")
        modes["frozen"] = [str(args.exe.resolve()), "--cli"]
    report = {"status": "failed", "platform": platform.platform(), "architecture": platform.machine(),
              "scope": "synthetic only; no private reference inputs", "customer_app_acceptance": "unverified", "runs": {}}
    try:
        for mode, command in modes.items():
            work = out / mode
            work.mkdir()
            env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
            if mode == "development":
                env["PYTHONPATH"] = str(ROOT / "src")
                env["QA_PARSER_WINDOWS_RESOURCES"] = str(ROOT / "build/windows/resources")
            else:
                for key in list(env):
                    if key.upper().startswith(("PYTHON", "QA_PARSER_")):
                        env.pop(key)
                system_root = os.environ.get("SystemRoot", r"C:\Windows")
                env["PATH"] = os.pathsep.join([str(Path(system_root) / "System32"), system_root]) if sys.platform == "win32" else "/usr/bin:/bin:/usr/sbin:/sbin"
            batch = work / "批量 结果"
            execute(command, inputs, batch, env, work / "batch.log")
            semantics = validate_batch(batch, png)
            (work / "semantics.json").write_text(json.dumps(semantics, ensure_ascii=False, indent=2), encoding="utf-8")
            encoded = json.dumps(semantics, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            report["runs"][mode] = {"status": "passed", "semantic_sha256": hashlib.sha256(encoded).hexdigest(),
                                    "files": [{"name": s["name"], "counts": s["counts"], "sheets": list(s["sheets"]),
                                               "omitted": s["omitted"]} for s in semantics]}
        if args.exe:
            require(report["runs"]["development"]["semantic_sha256"] == report["runs"]["frozen"]["semantic_sha256"],
                    "Source and relocated frozen semantics differ")
            report["source_frozen_equivalent"] = True
        report["status"] = "passed"
    finally:
        (out / "composite-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("COMPOSITE_REPORT " + json.dumps(report, ensure_ascii=False, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
