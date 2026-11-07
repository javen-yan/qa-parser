#!/usr/bin/env python3
"""Read-only reference regression; all generated artifacts go to a NEW --out.

This verifies structural consistency, determinism and export fidelity. It does
not certify formula semantics, the customer's import contract, or Windows.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from qa_parser.core import load_task, run_document, validate_task  # noqa: E402


HEADERS = {
    "choice": ["ID", "类型", "困难程度", "题目", "选择项", "答案", "解析", "sid", "optionA", "optionB", "optionC", "optionD", "title", "questionType"],
    "blank": ["ID", "考点", "题目", "答案"],
}
REPRESENTATIVES = ("微软公式化学.docx", "MathType公式化学填空.docx")
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def check(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_xlsx(path: Path) -> list[list[str]]:
    """Independent standard-library ZIP/XML inspection, not XlsxWriter readback."""
    with ZipFile(path) as archive:
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = ["".join(t.text or "" for t in si.findall(".//s:t", NS))
                       for si in ET.fromstring(archive.read("xl/sharedStrings.xml"))]
        root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    check(not root.findall(".//s:f", NS), "XLSX contains unintended calculation formulas")
    result = []
    for row in root.findall(".//s:sheetData/s:row", NS):
        values = {}
        for cell in row.findall("s:c", NS):
            address = cell.get("r", "")
            col = 0
            for letter in address:
                if letter.isalpha():
                    col = col * 26 + ord(letter.upper()) - ord("A") + 1
                else:
                    break
            check(col > 0, f"Invalid XLSX cell address {address}")
            kind = cell.get("t")
            if kind == "inlineStr":
                value = "".join(t.text or "" for t in cell.findall(".//s:t", NS))
            elif kind == "s":
                value = strings[int(cell.findtext("s:v", namespaces=NS))]
            else:
                value = cell.findtext("s:v", default="", namespaces=NS)
                check(not value, f"Expected literal string cell at {address}, got {kind}")
            values[col - 1] = value
        width = max(values, default=-1) + 1
        result.append([values.get(i, "") for i in range(width)])
    return result


def check_ownership(task: dict, report: dict):
    blocks = {b["index"]: b for b in task["blocks"]}
    formulas = {f["id"]: f for f in task["formulas"]}
    seen_blocks = set()
    for question in report["questions"]:
        indices = question["block_indices"]
        check(not seen_blocks.intersection(indices), f"Blocks assigned to multiple questions: {question['number']}")
        seen_blocks.update(indices)
        locations = [blocks[index]["location"] for index in indices]
        for problem in question["issues"]:
            check(bool(problem.get("code")) and bool(problem.get("message")), f"Unexplained issue on question {question['number']}")
            if problem.get("formula_id"):
                fid = problem["formula_id"]
                check(fid in question["formula_ids"] and fid in formulas, f"Formula issue assigned to wrong question: {fid}")
            location = problem.get("location", "")
            # Structural issues without a token location inherit the question's
            # location; detailed OOXML locations must belong to its blocks.
            if location.startswith("word/document.xml:"):
                check(any(location == parent or location.startswith(parent + "/") for parent in locations),
                      f"Issue location is outside question {question['number']}: {location}")
        check(question["status"] == ("review" if question["issues"] else "passed"), "Issue/status mismatch")


def check_export(task: dict, report: dict, export_report: dict, out_dir: Path):
    rows = read_xlsx(out_dir / "questions.xlsx")
    columns = HEADERS[task["kind"]]
    check(rows and rows[0] == columns, "Export header differs from supplied template")
    included = [q for q in report["questions"] if q["status"] == "passed"]
    omitted = [q for q in report["questions"] if q["status"] == "review"]
    check(len(rows) == len(included) + 1, "XLSX row count differs from passed question count")
    check(export_report["exported_questions"] == len(included), "Export report row count differs")
    check([q["number"] for q in export_report["omitted_questions"]] == [q["number"] for q in omitted], "Omission list differs from review questions")
    check(export_report["partial"] == bool(omitted), "Export partial flag differs")
    for index, (row, question) in enumerate(zip(rows[1:], included), 2):
        check(len(row) == len(columns), f"XLSX row {index} has missing columns")
        values = dict(zip(columns, row))
        expected = {"题目": question["stem"], "答案": question["answer"], "ID": ""}
        if task["kind"] == "choice":
            expected.update({"解析": question["analysis"], "困难程度": question["difficulty"],
                             "类型": task["config"]["choice_type"], "title": question["stem"],
                             "sid": "", "questionType": task["config"]["question_type"],
                             "选择项": "\n".join(f"{k}．{v}" for k, v in sorted(question["options"].items())),
                             **{f"option{k}": question["options"][k] for k in "ABCD"}})
        else:
            expected["考点"] = question["knowledge_point"]
        for field, value in expected.items():
            check(values[field] == value, f"XLSX row {index} / {field} changed from parsed question")
        check(export_report["rows"][index - 2]["number"] == question["number"], "Export source-number mapping differs")
    manifest = {a["filename"]: a for a in task["assets"]}
    expected_images = {name for q in included for name in q["asset_filenames"]}
    actual_images = {p.name for p in (out_dir / "images").iterdir()}
    check(expected_images == actual_images, "Export image set differs from passed-question references")
    for filename in expected_images:
        check(sha(out_dir / "images" / filename) == manifest[filename]["sha256"], f"Export image content changed: {filename}")
    saved = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    check(saved == export_report, "Saved export report differs from returned report")


def comparable(task):
    copy = deepcopy(task)
    copy["source"].pop("original_path", None)
    # metadata_sha256 includes original_path, which is deliberately excluded.
    copy.pop("metadata_sha256", None)
    return copy


def check_fallbacks(task: dict, task_dir: Path):
    formulas = {f["id"]: f for f in task["formulas"]}
    assets = {a["id"]: a for a in task["assets"]}
    fallback_count = 0
    for formula in formulas.values():
        if formula["status"] != "image":
            check(not formula.get("fallback_asset_id"), "Non-image formula unexpectedly owns a fallback image")
            continue
        fallback_count += 1
        check(formula.get("fallback_asset_id") in assets, "Image formula has no associated asset")
        asset = assets[formula["fallback_asset_id"]]
        check(asset.get("origin") == "formula_preview", "Formula fallback origin not recorded")
        check(asset.get("formula_id") == formula["id"], "Fallback image belongs to a different formula")
        check((formula.get("preview_binding") or {}).get("verified") is True, "Fallback preview-to-formula binding was not verified")
        preview = formula.get("preview")
        check(preview is not None, "Image fallback has no original preview evidence")
        check(asset.get("source_preview_sha256") == preview["sha256"], "Fallback source-preview lineage differs")
        check(sha(task_dir / preview["path"]) == preview["sha256"], "Original formula preview bytes changed")
        check(sha(task_dir / asset["path"]) == asset["sha256"], "Fallback image bytes changed")
        # Raster output can differ from embedded WMF bytes. The explicit lineage
        # links that output to the preserved source; success alone is not visual QA.
        check(asset["extension"] == "png", "Formula image fallback must be portable PNG")
        check(bool(asset.get("engine")), "Formula image conversion engine not recorded")
        check(asset.get("width", 0) > 0 and asset.get("height", 0) > 0, "Formula image dimensions not recorded")
    for question in task["questions"]:
        expected = []
        for ref in re.findall(r"⟦([FI]\d{6})⟧", question["raw_text"]):
            if ref in assets:
                expected.append(assets[ref]["filename"])
            elif ref in formulas and formulas[ref]["status"] == "image":
                expected.append(assets[formulas[ref]["fallback_asset_id"]]["filename"])
        expected = list(dict.fromkeys(expected))
        check(question["asset_filenames"] == expected, f"Question {question['number']} image reference order differs from source")
        rendered = "\n".join([question["stem"], *[v for _, v in sorted(question["options"].items())], question["answer"], question["analysis"]])
        for filename in expected:
            check(task["config"]["image_reference"].format(filename=filename) in rendered, f"Missing rendered image reference: {filename}")
    return fallback_count


def run_one(source: Path, work: Path) -> dict:
    kind = "blank" if "填空" in source.stem else "choice"
    automatic = run_document(source, kind, work)
    task, task_dir = load_task(work / "task")
    report = json.loads((work / "task/report.json").read_text(encoding="utf-8"))
    check(len(task["questions"]) == 100, f"Expected 100 questions; found {len(task['questions'])}")
    check([q["number"] for q in task["questions"]] == [str(i) for i in range(1, 101)], "Source question numbers are not exactly 1..100 in order")
    check(all(q["answer"].strip() for q in task["questions"]), "A source question lost its answer")
    check_ownership(task, report)
    check_fallbacks(task, task_dir)
    validated = validate_task(task, task_dir, source)
    check(validated == report, "Reload validation differs from initial parse report")
    run_document(source, kind, work / "repeat")
    second = json.loads((work / "repeat/task/report.json").read_text(encoding="utf-8"))
    repeated, _ = load_task(work / "repeat/task")
    check(comparable(task) == comparable(repeated), "Repeated parsing changed task semantics")
    check(report == second, "Repeated parsing changed validation report")
    for resource in task["assets"]:
        check(sha(task_dir / resource["path"]) == sha(work / "repeat/task" / resource["path"]), "Repeated extraction changed image bytes")
    quarantine = json.loads((work / "quarantine.json").read_text(encoding="utf-8"))
    expected_quarantined = report["questions"] if report["document_issues"] else [q for q in report["questions"] if q["status"] == "review"]
    check(quarantine["total_quarantined"] == len(expected_quarantined), "Quarantine total differs from validation")
    check([q["number"] for q in quarantine["questions"]] == [q["number"] for q in expected_quarantined], "Quarantine source question list differs")
    check(automatic["quarantined"] == quarantine["total_quarantined"], "Run summary differs from quarantine total")
    check(automatic["total"] == report["total"], "Run summary total differs from parsed question count")
    check(all(automatic["formulas"].get(k, 0) == report["formulas"].get(k, 0) for k in ("total", "converted", "image", "review")), "Run formula counters differ")
    passed_questions = [] if report["document_issues"] else [q for q in report["questions"] if q["status"] == "passed"]
    exported_ids = {fid for question in passed_questions for fid in question["formula_ids"]}
    exported_formulas = [f for f in task["formulas"] if f["id"] in exported_ids]
    check(automatic["passed"] == len(passed_questions), "Automatic passed count differs from safe rows")
    check(automatic["exported_formulas"]["total"] == len(exported_formulas), "Exported formula total differs")
    for state in ("converted", "image", "review"):
        check(automatic["exported_formulas"][state] == sum(f["status"] == state for f in exported_formulas), f"Exported formula {state} count differs")
    formula_map = {f["id"]: f for f in task["formulas"]}
    expected_fallback_questions = sum(any(formula_map[fid]["status"] == "image" for fid in question["formula_ids"]) for question in passed_questions)
    check(automatic["questions_with_formula_images"] == expected_fallback_questions, "Exported fallback-question count differs")
    export_state = "blocked_by_document_issues" if report["document_issues"] else "no_passed_questions" if not report["passed"] else "verified"
    exported = None
    if export_state == "verified":
        exported = json.loads((work / "export/report.json").read_text(encoding="utf-8"))
        check_export(task, report, exported, work / "export")
    else:
        check(not (work / "export/questions.xlsx").exists(), "Quarantined export produced an apparently successful empty workbook")
        check((work / "export/report.json").is_file(), "Empty export did not record its explanation")
    counts = Counter(problem["code"] for question in report["questions"] for problem in question["issues"])
    return {
        "source": source.name, "kind": kind, "checks": "passed", "total": report["total"],
        "passed": report["passed"], "review": report["review"], "formulas": report["formulas"],
        "automatic_status": automatic["status"], "quarantined": automatic["quarantined"],
        "questions_with_image_fallback_any": sum(any(next(f for f in task["formulas"] if f["id"] == fid)["status"] == "image" for fid in q["formula_ids"]) for q in task["questions"]),
        "exported_questions_with_image_fallback": expected_fallback_questions,
        "exported_formulas": automatic["exported_formulas"],
        "images": len(task["assets"]), "document_issues": report["document_issues"],
        "issue_counts": dict(sorted(counts.items())), "repeat_semantics": "identical",
        "export_check": export_state, "exported_questions": exported["exported_questions"] if exported else 0,
        "exported_images": len(exported["images"]) if exported else 0,
        "task": str(work / "task"), "export": str(work / "export") if exported else None,
    }


def refresh_summary(summary_path: Path) -> dict:
    """Refresh reporting fields only, without parsing or changing accepted data."""
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    for result in summary["documents"]:
        if result.get("checks") != "passed":
            continue
        work = summary_path.parent / Path(result["source"]).stem
        parsed = json.loads((work / "task/task.json").read_text(encoding="utf-8"))
        automatic = json.loads((work / "summary.json").read_text(encoding="utf-8"))
        exported = json.loads((work / "export/report.json").read_text(encoding="utf-8"))
        formula_map = {formula["id"]: formula for formula in parsed["formulas"]}
        exported_ids = {row["id"] for row in exported.get("rows", [])}
        included = [question for question in parsed["questions"] if question["id"] in exported_ids]
        check(len(included) == exported["exported_questions"], "Saved exported question count differs from task rows")
        formula_ids = {fid for question in included for fid in question["formula_ids"]}
        counts = {"total": len(formula_ids), **{state: sum(formula_map[fid]["status"] == state for fid in formula_ids) for state in ("converted", "image", "review")}}
        check(counts == automatic["exported_formulas"], "Saved automatic exported-formula counters differ from actual exported rows")
        any_images = sum(any(formula_map[fid]["status"] == "image" for fid in question["formula_ids"]) for question in parsed["questions"])
        exported_images = sum(any(formula_map[fid]["status"] == "image" for fid in question["formula_ids"]) for question in included)
        check(exported_images == automatic["questions_with_formula_images"], "Saved automatic fallback-question count differs from actual exported rows")
        result.pop("questions_with_image_fallback", None)
        result["questions_with_image_fallback_any"] = any_images
        result["exported_questions_with_image_fallback"] = exported_images
        result["exported_formulas"] = counts
    summary["totals"].pop("questions_with_image_fallback", None)
    for key in ("questions_with_image_fallback_any", "exported_questions_with_image_fallback"):
        summary["totals"][key] = sum(result.get(key, 0) for result in summary["documents"])
    summary["exported_formula_totals"] = {key: sum(result.get("exported_formulas", {}).get(key, 0) for result in summary["documents"]) for key in ("total", "converted", "image", "review")}
    # Only this aggregate summary is replaced. All tasks, workbooks, original
    # previews, images, quarantine files and per-document reports remain untouched.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=summary_path.parent, prefix=".summary-refresh-", suffix=".json", delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(summary, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    try:
        os.replace(temporary, summary_path)
    finally:
        temporary.unlink(missing_ok=True)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, default=ROOT / "reference")
    parser.add_argument("--out", type=Path, required=True, help="New results directory; never overwritten")
    parser.add_argument("--representatives", action="store_true", help="Only one OMML choice and one MathType blank document")
    parser.add_argument("--baseline", type=Path, default=ROOT / "outputs/mac-acceptance/summary.json", help="Prior read-only reference result used for numeric comparison")
    parser.add_argument("--refresh-summary", action="store_true", help="Refresh reporting fields in an existing --out/summary.json only; do not reparse or modify accepted artifacts")
    args = parser.parse_args(argv)
    if args.refresh_summary:
        summary = refresh_summary(args.out / "summary.json")
        print(json.dumps({"summary": str(args.out / "summary.json"), "totals": summary["totals"], "formula_totals": summary["formula_totals"], "exported_formula_totals": summary["exported_formula_totals"]}, ensure_ascii=False, indent=2))
        return 0
    sources = [args.reference / name for name in REPRESENTATIVES] if args.representatives else sorted(args.reference.glob("*.docx"))
    if not sources or any(not source.is_file() for source in sources):
        parser.error("Reference documents missing")
    try:
        args.out.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error(f"Output already exists: {args.out}")
    results = []
    for source in sources:
        try:
            result = run_one(source, args.out / source.stem)
        except Exception as exc:
            result = {"source": source.name, "checks": "failed", "error": f"{type(exc).__name__}: {exc}"}
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    summary = {
        "schema_version": 1, "scope": "representatives" if args.representatives else "all_reference",
        "checks": "failed" if any(r["checks"] == "failed" for r in results) else "passed",
        "documents": results,
        "totals": {key: sum(r.get(key, 0) for r in results) for key in ("total", "passed", "review", "quarantined", "questions_with_image_fallback_any", "exported_questions_with_image_fallback", "images", "exported_questions", "exported_images")},
        "formula_totals": {key: sum(r.get("formulas", {}).get(key, 0) for r in results) for key in ("total", "converted", "image", "review")},
        "exported_formula_totals": {key: sum(r.get("exported_formulas", {}).get(key, 0) for r in results) for key in ("total", "converted", "image", "review")},
        "acceptance": {"automated_structure_and_repeatability": "failed" if any(r["checks"] == "failed" for r in results) else "passed", "formula_semantics": "not_certified_by_this_script", "target_system_import": "unverified", "windows": "unverified"},
    }
    if args.baseline.is_file():
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        names = {source.name for source in sources}
        previous = [row for row in baseline["documents"] if row["source"] in names]
        summary["baseline_comparison"] = {
            "path": str(args.baseline), "matched_documents": len(previous),
            "before": {k: sum(row.get(k, 0) for row in previous) for k in ("total", "passed", "review", "exported_questions")},
            "after": {k: summary["totals"][k] for k in ("total", "passed", "review", "exported_questions")},
            "note": "Counts compare the same reference filenames; image fallback is a representation change, not verified LaTeX conversion.",
        }
        summary["baseline_comparison"]["delta"] = {k: summary["baseline_comparison"]["after"][k] - summary["baseline_comparison"]["before"][k] for k in ("total", "passed", "review", "exported_questions")}
    else:
        summary["baseline_comparison"] = {"path": str(args.baseline), "status": "unavailable"}
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"checks": summary["checks"], "summary": str(args.out / "summary.json"), "totals": summary["totals"]}, ensure_ascii=False))
    return 1 if summary["checks"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
