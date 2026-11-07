#!/usr/bin/env python3
"""Exercise real batch modes and compare exports with the accepted Mac baseline.

Creates a new output tree only. Does not certify formula semantics, target APP
import, Windows, or UI behavior (the browser check is recorded separately).
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
from xml.sax.saxutils import escape
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from qa_parser.batch import run_batch
from qa_parser.classification import classify_document
from check_reference import read_xlsx
from PIL import Image


def check(value, message):
    if not value:
        raise AssertionError(message)


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def image_hashes(root):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((root / "images").glob("*")) if p.is_file()}


def same_export(actual, baseline):
    actual, baseline = Path(actual), Path(baseline)
    check(read_xlsx(actual / "questions.xlsx") == read_xlsx(baseline / "questions.xlsx"),
          f"Excel cell semantics differ from baseline: {actual}")
    check(image_hashes(actual) == image_hashes(baseline), f"Image order/content differ: {actual}")
    actual_report = json.loads((actual / "report.json").read_text())
    expected_report = json.loads((baseline / "report.json").read_text())
    check(actual_report["omitted_questions"] == expected_report["omitted_questions"],
          f"Omitted questions/reasons differ: {actual}")


def p(value):
    return '<w:p><w:r><w:t xml:space="preserve">' + escape(value) + '</w:t></w:r></w:p>'


def choice(number="1", word="甲", answer="A", picture=False):
    body = p(f"{number}．选择正确答案：{word}")
    if picture:
        body += '<w:p><w:r><w:drawing><a:blip r:embed="image"/></w:drawing></w:r></w:p>'
    return body + p("A．甲 B．乙 C．丙 D．丁") + p("【答案】" + answer)


def blank(number="1"):
    return p(f"{number}．填入正确内容____") + p("【答案】铁")


def fixture(path, body, color=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, "w") as z:
        z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><w:body>' + body + '</w:body></w:document>')
        if color:
            stream = io.BytesIO()
            Image.new("RGB", (8, 8), color).save(stream, format="PNG")
            z.writestr("word/media/picture.png", stream.getvalue())
            z.writestr("word/_rels/document.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="image" Target="media/picture.png" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"/></Relationships>')
    return path


def run_checked(sources, mode, output):
    events = []
    result = run_batch(sources, mode, output, progress=lambda event: events.append(deepcopy(event)))
    check(result["completed"] == len(sources), f"Batch failed to finish: {output}")
    check(len(result["files"]) == len(sources), f"Lost file: {output}")
    check(len({f["output"] for f in result["files"]}) == len(sources), f"Output collision: {output}")
    check(events and all(events[n]["completed"] <= events[n + 1]["completed"] for n in range(len(events) - 1)), "Non-monotonic batch progress")
    check(any(any(f["status"] == "running" for f in e["files"]) for e in events), "No running progress was emitted")
    for f in result["files"]:
        check(f["status"] in {"success", "partial_failure", "failure"}, "File remained pending")
        check(Path(f["report"]).is_file(), "Missing readable per-file report")
    save(output / "validation-progress.json", events)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, default=ROOT / "outputs/customer-flow-validation")
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    evidence = {"checks": "running", "scope": "Mac batch integration", "cases": []}
    save(args.out / "validation.json", evidence)
    references = sorted((ROOT / "reference").glob("*.docx"))
    check(len(references) == 9, "Expected nine reference DOCX files")
    auto = run_checked(references, "auto", args.out / "auto-all")
    check(sum(f["total"] for f in auto["files"]) == 900, "Reference question total changed")
    check(sum(f["passed"] for f in auto["files"]) == 831, "Reference exported baseline changed")
    check(sum(f["quarantined"] for f in auto["files"]) == 69, "Reference omissions changed")
    baseline_summary = json.loads((args.baseline / "summary.json").read_text())
    baseline_by_name = {d["source"]: d for d in baseline_summary["documents"]}
    for source, f in zip(references, auto["files"]):
        expected = baseline_by_name[source.name]
        check(f["total"] == 100 and f["passed"] == expected["passed"], f"Count mismatch: {source.name}")
        kind = classify_document(source)
        check(kind["status"] == "classified" and kind["kind"] == expected["kind"], f"Wrong type: {source.name}")
        same_export(f["export"], args.baseline / source.stem / "export")
    evidence["cases"].append({"case": "automatic_all_nine", "checks": "passed", "total": 900, "exported": 831, "omitted": 69, "comparison": "all Excel cell strings, image names/SHA256, omitted questions and reasons identical to baseline"})
    print("Automatic nine: 900 detected / 831 exported / 69 omitted; baseline semantics match", flush=True)

    for kind, filename in [("choice", "微软公式化学.docx"), ("blank", "MathType公式化学填空.docx")]:
        source = ROOT / "reference" / filename
        manual = run_checked([source], kind, args.out / ("manual-" + kind))
        same_export(manual["files"][0]["export"], args.baseline / source.stem / "export")
        evidence["cases"].append({"case": "manual_" + kind, "checks": "passed", "result": manual["files"][0]["status"]})
    print("Manual choice and blank exports match baseline", flush=True)

    inputs = args.out / "fixtures"
    first = fixture(inputs / "first" / "同名 空格.docx", choice(word="红色", picture=True), "red")
    second = fixture(inputs / "second" / "同名 空格.docx", choice(word="蓝色", answer="B", picture=True), "blue")
    corrupted = inputs / "损坏.docx"
    corrupted.write_bytes(b"not a docx")
    edge = run_checked([first, corrupted, second], "auto", args.out / "same-name-and-corrupt")
    check([f["status"] for f in edge["files"]] == ["success", "failure", "success"], "A bad document contaminated neighbors")
    left, right = Path(edge["files"][0]["export"]), Path(edge["files"][2]["export"])
    check(image_hashes(left).keys() == image_hashes(right).keys(), "Resource numbering did not restart per file")
    check(image_hashes(left) != image_hashes(right), "Cross-file image contamination")
    check(read_xlsx(left / "questions.xlsx") != read_xlsx(right / "questions.xlsx"), "Same-name questions were overwritten")
    evidence["cases"].append({"case": "same_basename_chinese_space_and_one_corrupt", "checks": "passed", "statuses": [f["status"] for f in edge["files"]], "independent_image_names": list(image_hashes(left))})

    mixed = fixture(inputs / "mixed.docx", choice() + blank("2"))
    unknown = fixture(inputs / "unknown.docx", p("1．请讨论你的观点") + p("【答案】观点如下"))
    disguised = fixture(inputs / "选择题.docx", blank())
    evidence["classifications"] = {f.name: classify_document(f) for f in [mixed, unknown, disguised]}
    check(evidence["classifications"][mixed.name]["status"] == "mixed", "Mixed document was guessed")
    check(evidence["classifications"][unknown.name]["status"] == "unknown", "Unknown document was guessed")
    check(evidence["classifications"][disguised.name]["kind"] == "blank", "Classifier relied on filename")
    rejected = run_checked([mixed, unknown], "auto", args.out / "rejected-auto")
    check(all(f["status"] == "failure" for f in rejected["files"]), "Unsafe automatic classification exported")
    mismatch = run_checked([disguised], "choice", args.out / "rejected-manual")
    check(mismatch["files"][0]["status"] == "failure", "Manual mismatch silently exported")
    for f in rejected["files"] + mismatch["files"]:
        check(not list(Path(f["output"]).rglob("questions.xlsx")), "Rejected input produced import workbook")
    evidence["cases"].append({"case": "mixed_unknown_manual_mismatch_filename_independence", "checks": "passed"})
    evidence["checks"] = "passed"
    evidence["limitations"] = ["Mac only", "No target APP import/rendering verification", "Browser interactions recorded separately", "No complete human formula semantic review"]
    save(args.out / "validation.json", evidence)
    print(f"All batch checks passed: {args.out / 'validation.json'}", flush=True)


if __name__ == "__main__":
    main()
