"""Independent, persistent batch jobs shared by CLI and local UI.

Progress callbacks receive full JSON-safe copies; notification failures never
cancel conversion. Output roots must be new. Numbered file directories prevent
same-name inputs sharing questions, assets, or reports.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import hashlib
import re
import shutil

from .classification import classify_document
from .core import run_document
from .document import MAX_ARCHIVE
from .delivery import publish_delivery, validate_delivery_names


def _write_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _slug(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:80] or "Word"


def _failure(folder: Path, row: dict, message: str, classification=None, diagnostic=None):
    folder.mkdir(parents=True, exist_ok=True)
    row.update(status="failure", message="转换失败：" + message, reasons=[message],
               export=None, report=str(folder / "result.txt"))
    if classification is not None:
        row["affected_questions"] = [{"number": q["number"], "reasons": [message]}
                                     for q in classification["questions"]]
        count = None if classification["status"] == "failed" else classification["total"]
        row["total"] = count
        row["quarantined"] = count
        _write_json(folder / "classification.json", classification)
    lines = [row["message"]]
    if row["total"] is not None:
        lines.append(f"已导出 0/{row['total']} 题。")
    if classification:
        unknown = [q["number"] for q in classification["questions"] if q["kind"] == "unknown"]
        if unknown:
            lines.append("无法可靠判断的题号：" + "、".join(unknown))
        lines.append("这份文档未生成 Excel；批量中的其他文档会继续处理。")
    (folder / "result.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _write_json(folder / "summary.json", {**row, "diagnostic": diagnostic})


def _aggregate(snapshot: dict) -> None:
    rows = snapshot["files"]
    finished = [row for row in rows if row["status"] not in ("queued", "running")]
    snapshot["completed"] = len(finished)
    snapshot["passed"] = sum(row["passed"] or 0 for row in rows)
    snapshot["quarantined"] = sum(row["quarantined"] or 0 for row in rows)
    snapshot["counts"] = {status: sum(row["status"] == status for row in rows)
                          for status in ("success", "partial_failure", "failure")}
    snapshot["question_counts"] = {key: sum(row.get("counts", {}).get(key, 0) for row in rows)
                                   for key in ("original_questions", "subquestions", "output_units", "exported", "policy_skipped", "failed_or_review")}
    if len(finished) != len(rows):
        snapshot["status"] = "running"
        snapshot["message"] = f"正在处理：{len(finished)}/{len(rows)} 份 Word 已完成。"
    elif all(row["status"] == "success" for row in rows):
        snapshot["status"] = "success"
        snapshot["message"] = f"全部成功：{len(rows)} 份 Word，共导出 {snapshot['passed']} 题。"
    elif snapshot["passed"]:
        snapshot["status"] = "partial_failure"
        snapshot["message"] = f"部分失败：共导出 {snapshot['passed']} 题。结果不完整，请查看各文件结果。"
    else:
        snapshot["status"] = "failure"
        snapshot["message"] = "转换失败：未生成可用的题目导出文件，请查看各文件原因。"


def run_batch(inputs: list[Path], mode: str, out: Path, progress=None, *,
              delivery_directory: Path | None = None) -> dict:
    """Convert auto/choice/blank inputs separately, continuing after file faults.

    ``out`` must not exist; ``progress(snapshot)`` receives queued/running and
    terminal file states, absolute output/report paths, and question counts.
    Invalid batch arguments/output permissions raise before starting. Per-file
    faults become explicit failure rows and reports, never a successful empty
    workbook. Input snapshots prevent source changes between detection/parse.
    Final-only ZIP/report goes into a new ``delivery_directory`` (default
    ``out / "最终结果"``); all intermediate files remain in ``out``.
    """
    if mode not in ("auto", "choice", "blank"):
        raise ValueError("Mode must be auto, choice or blank")
    if not inputs:
        raise ValueError("No Word inputs")
    sources = [Path(source).expanduser().absolute() for source in inputs]
    validate_delivery_names(source.name for source in sources)
    out = Path(out).expanduser().absolute()
    out.mkdir(parents=True, exist_ok=False)
    rows = []
    for index, source in enumerate(sources, 1):
        folder = out / f"{index:04d}-{_slug(source.stem)}"
        rows.append({"id": str(index), "name": source.name, "source": str(source),
                     "status": "queued", "kind": None, "total": None, "passed": 0,
                     "quarantined": None, "message": "等待处理", "reasons": [],
                     "output": str(folder), "export": None, "report": None})
    snapshot = {"schema_version": 1, "mode": mode, "status": "running", "completed": 0,
                "total_files": len(rows), "files": rows, "output": str(out),
                "report": str(out / "result.txt"), "delivery": None, "delivery_report": None,
                "delivery_directory": str(Path(delivery_directory).expanduser().absolute() if delivery_directory else out / "最终结果"),
                "delivery_status": "pending", "acceptance": {"system_import": "unverified", "windows": "unverified"}}

    def publish():
        _aggregate(snapshot)
        if snapshot["completed"] == snapshot["total_files"]:
            if snapshot["delivery_status"] == "pending":
                snapshot.update(status="running", message="正在整理最终结果包…")
            elif snapshot["delivery_status"] == "failure":
                snapshot.update(status="failure", message="最终结果包未能生成；转换记录已保留，请查看诊断信息。")
        lines = [snapshot["message"], ""]
        for item in rows:
            lines.extend([item["name"], item["message"], f"结果位置：{item['output']}", ""])
        lines.append("文件转换不表示目标系统导入或公式显示已完成验收。")
        report_temp = out / "result.txt.tmp"
        report_temp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report_temp.replace(out / "result.txt")
        _write_json(out / "batch-summary.json", snapshot)
        if progress:
            try:
                progress(deepcopy(snapshot))
            except Exception:
                # A closed browser/observer cannot make a file fail conversion.
                pass

    publish()
    for index, (source, row) in enumerate(zip(sources, rows), 1):
        folder = Path(row["output"])
        row.update(status="running", message="正在读取并识别题型")
        publish()
        classification = None
        try:
            if source.suffix.lower() != ".docx":
                _failure(folder, row, "目前只支持 .docx 格式的 Word。")
                continue
            if source.stat().st_size > MAX_ARCHIVE:
                _failure(folder, row, "文件过大，无法处理。")
                continue
            source_copy = out / ".inputs" / f"{index:04d}" / source.name
            source_copy.parent.mkdir(parents=True)
            shutil.copyfile(source, source_copy)
            if source_copy.stat().st_size > MAX_ARCHIVE:
                raise ValueError("Input grew beyond the size limit during snapshot")
            row["source_snapshot"] = str(source_copy)
            row["source_sha256"] = hashlib.sha256(source_copy.read_bytes()).hexdigest()
            classification = classify_document(source_copy)
            row["classification_status"] = classification["status"]
            row["total"] = classification["total"]
            if classification["status"] == "failed" or not classification["total"]:
                _failure(folder, row, classification["reason"], classification)
                continue
            detected = classification["kind"]
            row["kind"] = detected or ("mixed" if classification["status"] == "mixed" else "unknown")
            if mode != "auto" and classification["status"] == "classified" and mode != detected:
                _failure(folder, row, "所选题型与文档内容不一致，未进行转换。", classification)
                continue
            row.update(message="正在转换并检查题目")
            publish()
            summary = run_document(source_copy, mode, folder)
            row["counts"] = summary.get("counts", {})
            _write_json(folder / "classification.json", classification)
            user = summary["user_result"]
            if summary.get("special_questions"):
                row["special_questions"] = str(folder / summary["special_questions"]["path"])
                row["special_questions_sha256"] = summary["special_questions"]["sha256"]
                row["special_questions_total"] = summary["special_questions"]["total"]
            if summary.get("export_preview"):
                row["export_preview"] = str(folder / summary["export_preview"]["path"])
                row["export_preview_sha256"] = summary["export_preview"]["sha256"]
                row["display_assets"] = [{**asset, "path": str(folder / asset["path"])}
                                         for asset in summary["display_assets"]]
            if user.get("preview_notice"):
                row["preview_notice"] = user["preview_notice"]
            if summary.get("problem_preview"):
                row["problem_preview"] = str(folder / summary["problem_preview"]["path"])
                row["problem_preview_sha256"] = summary["problem_preview"]["sha256"]
            if summary.get("problem_preview_error"):
                row["preview_notice"] = user.get("preview_notice")
            row.update(status=user["status"], total=summary["total"], passed=summary["passed"],
                       quarantined=summary["quarantined"], message=user["message"], reasons=user["reasons"],
                       export=str(folder / "export") if user["export"] else None,
                       report=str(folder / "result.txt"), affected_questions=user["affected_questions"])
        except Exception as exc:
            try:
                _failure(folder, row, "无法读取文件或保存结果，请检查文件和输出位置。",
                         classification, f"{type(exc).__name__}: {exc}")
            except OSError:
                row.update(status="failure", message="转换失败：无法保存结果。", reasons=["无法保存结果"],
                           export=None, report=None)
        finally:
            publish()
    _aggregate(snapshot)
    try:
        snapshot.update(publish_delivery(snapshot, Path(snapshot["delivery_directory"])))
    except Exception as exc:
        snapshot.update(delivery_status="failure", delivery_error=f"{type(exc).__name__}: {exc}")
    publish()
    return deepcopy(snapshot)
