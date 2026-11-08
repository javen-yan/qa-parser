"""Template-oriented XLSX export. No Office installation or external process required.

The parser owns question validation; this layer additionally checks export limits,
resource integrity and the explicit partial-export policy. It never resolves issues.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

import xlsxwriter


CHOICE_COLUMNS = [
    "ID", "类型", "困难程度", "题目", "选择项", "答案", "解析", "sid",
    "optionA", "optionB", "optionC", "optionD", "title", "questionType",
]
BLANK_COLUMNS = ["ID", "考点", "题目", "答案"]
_IMAGE_REF = re.compile(r"(?<![\w.-])(\d{4,}\.(?:png|jpe?g|gif|bmp|tiff?|wmf|emf|svg|webp))(?![\w.-])", re.I)


class ExportError(ValueError):
    """A task cannot be exported without losing information or violating policy."""


def _string(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value)
    raise ExportError(f"Expected a text field, received {type(value).__name__}")


def _check_cell(text: str, label: str) -> None:
    # Excel's limit is checked conservatively in UTF-16 code units too, so emoji
    # cannot evade it. Never rely on XlsxWriter's silent truncation behavior.
    try:
        units = len(text.encode("utf-16-le")) // 2
    except UnicodeEncodeError as exc:
        raise ExportError(f"Invalid Unicode in {label}") from exc
    if len(text) > 32767 or units > 32767:
        raise ExportError(f"Excel cell limit exceeded in {label} (32767 characters)")


def _row(question: dict, kind: str, config: dict, columns: list[str]) -> list[str]:
    options = question.get("options", {})
    if not isinstance(options, dict):
        raise ExportError("Question options must be an object")
    values = {
        "ID": "", "sid": "", "questionType": config.get("question_type", ""),
        "类型": config.get("choice_type", "单选题"),
        "困难程度": question.get("difficulty", ""),
        "题目": question.get("stem", ""), "title": question.get("stem", ""),
        "选择项": "\n".join(f"{letter}．{_string(value)}" for letter, value in sorted(options.items())),
        "答案": question.get("answer", ""), "解析": question.get("analysis", ""),
        "考点": question.get("knowledge_point", ""),
        **{f"option{letter}": options.get(letter, "") for letter in "ABCD"},
    }
    defaults = config.get("field_defaults", {})
    if not isinstance(defaults, dict):
        raise ExportError("field_defaults must be an object keyed by column name")
    for column, value in defaults.items():
        if not values.get(column):
            values[column] = value
    # Optional mapping enables a verified import contract without changing parsing.
    for column, field in config.get("column_fields", {}).items():
        if field not in question:
            raise ExportError(f"Mapped question field does not exist: {field}")
        values[column] = question[field]
    unknown = set(columns) - set(values)
    if unknown:
        raise ExportError(f"No mapping/default for columns: {sorted(unknown)}")
    result = [_string(values[column]) for column in columns]
    for column, value in zip(columns, result):
        _check_cell(value, f"question {question.get('number', '?')} / {column}")
        if column in config.get("required_columns", []) and not value.strip():
            raise ExportError(f"Required column {column} empty in question {question.get('number', '?')}")
    return result


def _safe_resource(task_dir: Path, asset: dict) -> Path:
    name = asset.get("filename", "")
    if not isinstance(name, str) or not name or name in (".", "..") or "/" in name or "\\" in name:
        raise ExportError(f"Invalid asset filename: {name!r}")
    relative = Path(asset.get("path", ""))
    if relative.is_absolute() or ".." in relative.parts:
        raise ExportError(f"Unsafe asset path: {relative}")
    root = task_dir.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ExportError(f"Asset missing or outside task directory: {name}")
    if path.suffix.lower() != Path(name).suffix.lower():
        raise ExportError(f"Asset extension mismatch: {name}")
    expected = asset.get("sha256")
    if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ExportError(f"Missing or invalid asset SHA-256: {name}")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ExportError(f"Asset integrity mismatch: {name}")
    return path


def validate_export_question(question: dict, kind: str, config: dict) -> None:
    """Preflight one row so unattended callers can quarantine it individually."""
    columns = config.get(f"{kind}_columns", CHOICE_COLUMNS if kind == "choice" else BLANK_COLUMNS)
    _row(question, kind, config, columns)


def export_task(task: dict, task_dir: Path, out_dir: Path, allow_partial: bool = False) -> dict:
    """Export validated questions and referenced resources to a new directory.

    Raises ExportError before publishing any result on validation failure. The
    report explicitly states that the target application's import is unverified.
    """
    task_dir, out_dir = Path(task_dir), Path(out_dir)
    if out_dir.exists() or out_dir.is_symlink():
        raise ExportError(f"Output already exists: {out_dir}")
    if task.get("schema_version") != 1:
        raise ExportError("Unsupported task schema_version")
    kind = task.get("kind")
    if kind not in ("choice", "blank"):
        raise ExportError(f"Unsupported question kind: {kind}")
    config = task.get("config", {})
    columns = config.get(f"{kind}_columns", CHOICE_COLUMNS if kind == "choice" else BLANK_COLUMNS)
    if not isinstance(columns, list) or not columns or not all(isinstance(c, str) and c for c in columns):
        raise ExportError("Columns must be a nonempty array of names")
    if len(columns) != len(set(columns)) or len(columns) > 16384:
        raise ExportError("Duplicate columns or Excel column limit exceeded")
    required = config.get("required_columns", [])
    if not isinstance(required, list) or not all(isinstance(c, str) for c in required):
        raise ExportError("required_columns must be an array of column names")
    if set(required) - set(columns):
        raise ExportError("required_columns contains columns absent from the export template")
    for column in columns:
        _check_cell(column, "header")
    questions = task.get("questions", [])
    omitted = [{"id": q.get("id"), "number": q.get("number"), "issues": q["issues"]}
               for q in questions if q.get("issues")]
    if omitted and not allow_partial:
        raise ExportError(f"{len(omitted)} questions require review; explicitly enable partial export to omit them")
    included = [q for q in questions if not q.get("issues")]
    if not included:
        raise ExportError("No passed questions to export")
    if len(included) > 1048575:
        raise ExportError("Excel row limit exceeded")
    rows = [_row(q, kind, config, columns) for q in included]
    assets = {}
    for asset in task.get("assets", []):
        name = asset.get("filename")
        if name in assets:
            raise ExportError(f"Duplicate asset filename: {name}")
        assets[name] = asset
    selected_names = set()
    for question, row in zip(included, rows):
        declared = question.get("asset_filenames", [])
        if not isinstance(declared, list) or not all(isinstance(n, str) for n in declared):
            raise ExportError("asset_filenames must be a list of filenames")
        selected_names.update(declared)
        combined = "\n".join(row)
        selected_names.update(_IMAGE_REF.findall(combined))
        # Also honor arbitrary filenames in a caller's resource manifest.
        selected_names.update(name for name in assets if isinstance(name, str) and name in combined)
    missing = selected_names - assets.keys()
    if missing:
        raise ExportError(f"Referenced images missing from manifest: {sorted(missing)}")
    resources = [(assets[name], _safe_resource(task_dir, assets[name])) for name in sorted(selected_names)]
    report = {
        "schema_version": 1,
        "source": task.get("source", {}),
        "kind": kind,
        "image_reference": config.get("image_reference", "参考{filename}"),
        "import_contract": "unverified",
        "partial_requested": allow_partial,
        "partial": bool(omitted),
        "total_questions": len(questions),
        "exported_questions": len(included),
        "omitted_questions": omitted,
        "rows": [{"excel_row": i + 2, "id": q.get("id"), "number": q.get("number")}
                 for i, q in enumerate(included)],
        "images": [{key: a[key] for key in ("filename", "sha256", "origin", "formula_id", "source_preview_sha256", "engine", "width", "height") if key in a} for a, _ in resources],
        "formula_outcomes": [{key: f[key] for key in ("id", "format", "status", "location", "reason", "fallback_asset_id", "fallback_engine", "output_policy", "raster_evidence") if key in f}
                             for f in task.get("formulas", []) if any(f["id"] in q.get("formula_ids", []) for q in included)],
        "notes": ["Generated against a provisional column contract; target-system import and rendering have not been verified."],
    }
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{out_dir.name}-", dir=out_dir.parent))
    try:
        image_dir = temporary / "images"
        image_dir.mkdir()
        for asset, path in resources:
            destination = image_dir / asset["filename"]
            shutil.copyfile(path, destination)
            if hashlib.sha256(destination.read_bytes()).hexdigest() != asset["sha256"]:
                raise ExportError(f"Asset changed while exporting: {asset['filename']}")
        with xlsxwriter.Workbook(temporary / "questions.xlsx", {
            "strings_to_formulas": False, "strings_to_urls": False,
            "constant_memory": True,
        }) as workbook:
            worksheet = workbook.add_worksheet("选择" if kind == "choice" else "考点")
            header = workbook.add_format({"bold": True, "bg_color": "#E8EDF2", "border": 1})
            text = workbook.add_format({"text_wrap": True, "valign": "top", "num_format": "@"})
            worksheet.freeze_panes(1, 0)
            worksheet.set_column(0, len(columns) - 1, 20)
            for i, column in enumerate(columns):
                if column in ("题目", "选择项", "解析", "title"):
                    worksheet.set_column(i, i, 60)
            for row_index, row in enumerate([columns, *rows]):
                for col_index, value in enumerate(row):
                    result = worksheet.write_string(row_index, col_index, value, header if row_index == 0 else text)
                    if result:
                        raise ExportError(f"XLSX writer rejected cell {row_index + 1}:{col_index + 1} ({result})")
        (temporary / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # Avoid replacing existing results, including a target created during work.
        # Reserve the destination before moving completed files. A failed publish
        # removes only the directory created here, never a pre-existing directory.
        try:
            out_dir.mkdir()
        except FileExistsError as exc:
            raise ExportError(f"Output appeared during export: {out_dir}") from exc
        try:
            # Publish the completion marker last, independent of directory order.
            for name in ("images", "questions.xlsx", "report.json"):
                os.replace(temporary / name, out_dir / name)
        except BaseException:
            shutil.rmtree(out_dir)
            raise
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return report
