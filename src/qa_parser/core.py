"""Business rules and component orchestration, independent of CLI or GUI."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import re
import shutil
import tempfile

from .config import config_digest, digest, load_config, validate_config

QUESTION_START = re.compile(r"^\s*(\d{1,5})([．、]|\.(?!\d))")
SECTION = re.compile(r"【(答案|详解|解析|分析|点睛)】")
OPTION = re.compile(r"(?<![A-Za-z])([A-H])[．.、]")
REF = re.compile(r"⟦([FI]\d{6})⟧")


def issue(code: str, message: str, location: str = "", **extra) -> dict:
    return {"code": code, "message": message, "location": location, **extra}


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def safe_file(root: Path, relative: str) -> Path:
    """Task files may only reference regular files inside the task directory."""
    p = Path(relative)
    if p.is_absolute() or ".." in p.parts or "\\" in relative:
        raise ValueError(f"Unsafe task resource path: {relative}")
    resolved = (root / p).resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise ValueError(f"Missing or unsafe task resource: {relative}")
    return resolved


def _save_bytes(root: Path, relative: str, data: bytes) -> dict:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"path": relative, "sha256": digest(data), "size": len(data)}


def _jsonable(value):
    if isinstance(value, bytes):
        return {"bytes_sha256": digest(value), "size": len(value)}
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(v) for v in value]
    return value


def _prepare_blocks(document: dict, root: Path, config: dict):
    from .formulas import convert_mathtype, convert_omml

    blocks, formulas, assets = [], [], []
    for block in document["blocks"]:
        converted = {k: _jsonable(v) for k, v in block.items() if k != "tokens"}
        converted["tokens"] = []
        text = []
        for token in block["tokens"]:
            kind = token["kind"]
            loc = token.get("location", block.get("location", ""))
            if kind == "text":
                converted["tokens"].append(_jsonable(token))
                value = token["text"]
                if REF.search(value):
                    converted["tokens"].append({"kind": "unsupported", "reason": "Source text collides with internal reference marker syntax", "location": loc})
                    # Keep original XML and show the literal rather than substituting another object.
                    value = value.replace("⟦", "［").replace("⟧", "］")
                # Preserve visible blank lines that Word encodes as underlined spaces.
                text.append("_" * max(3, len(value)) if token.get("underline") and value and not value.strip() else value)
            elif kind == "formula":
                fid = f"F{len(formulas)+1:06d}"
                fmt = token["format"]
                data = token["data"]
                ext = "bin" if fmt == "mathtype" else "xml" if fmt == "omml" else "tex"
                resource = _save_bytes(root, f"evidence/{fid}.{ext}", data)
                if fmt == "omml":
                    result = convert_omml(data)
                elif fmt == "mathtype":
                    result = convert_mathtype(data)
                elif fmt == "latex":
                    result = {"latex": data.decode("utf-8"), "status": "converted", "reason": "", "engine": "docx-run-script"}
                else:
                    result = {"latex": None, "status": "review", "reason": f"Unsupported formula format {fmt}", "engine": "none"}
                if token.get("review_reason"):
                    result["status"] = "review"
                    result["reason"] = token["review_reason"]
                entry = {"id": fid, "format": fmt, "location": loc, "source": resource,
                         "preview_binding": token.get("preview_binding"), **result}
                for metadata in ("origin", "source_locations", "word_segments"):
                    if metadata in token:
                        entry[metadata] = token[metadata]
                if token.get("preview"):
                    suffix = str(token.get("preview_ext") or "bin").lstrip(".").lower()
                    if not re.fullmatch("[a-z0-9]{1,8}", suffix):
                        suffix = "bin"
                    entry["preview"] = _save_bytes(root, f"evidence/{fid}-preview.{suffix}", token["preview"])
                if entry["status"] != "converted" and config.get("formula_fallback", "isolate") == "image":
                    # Only the exact embedded preview associated with this object
                    # may replace its failed textual conversion. No guessed TeX.
                    binding = token.get("preview_binding") or {}
                    if not token.get("preview") or not binding.get("verified"):
                        entry["fallback_error"] = binding.get("reason", "No reliable original preview; no verified offline renderer for this source structure")
                    else:
                        from .formula_images import preview_to_png
                        image_result = preview_to_png(token["preview"], token.get("preview_ext", ""))
                        if image_result["status"] != "converted":
                            entry["fallback_error"] = image_result.get("reason", "Original preview conversion failed")
                        else:
                            iid = f"I{len(assets)+1:06d}"
                            filename = f"{len(assets)+1:04d}.png"
                            image_resource = _save_bytes(root, f"assets/{filename}", image_result["png"])
                            assets.append({"id": iid, "filename": filename, "extension": "png", "location": loc,
                                           "origin": "formula_preview", "formula_id": fid,
                                           "source_preview_sha256": entry["preview"]["sha256"],
                                           "width": image_result["width"], "height": image_result["height"],
                                           "engine": image_result["engine"], "review_reason": "", **image_resource})
                            entry.update(status="image", fallback_asset_id=iid,
                                         fallback_engine=image_result["engine"])
                            if image_result.get("font_evidence"):
                                entry["fallback_font_evidence"] = image_result["font_evidence"]
                formulas.append(entry)
                converted["tokens"].append({"kind": "formula", "id": fid, "location": loc})
                text.append(f"⟦{fid}⟧")
            elif kind == "image":
                iid = f"I{len(assets)+1:06d}"
                ext = str(token.get("extension", "bin")).lstrip(".").lower()
                if not re.fullmatch("[a-z0-9]{1,8}", ext):
                    ext = "bin"
                filename = f"{len(assets)+1:04d}.{ext}"
                resource = _save_bytes(root, f"assets/{filename}", token["data"])
                assets.append({"id": iid, "filename": filename, "extension": ext, "location": loc, "review_reason": token.get("review_reason", ""), **resource})
                converted["tokens"].append({"kind": "image", "id": iid, "location": loc})
                text.append(f"⟦{iid}⟧")
            else:
                converted["tokens"].append(_jsonable(token))
                text.append("【内容待复核】")
        converted["text"] = "".join(text)
        blocks.append(converted)
    return blocks, formulas, assets


def _split_fields(raw: str, kind: str) -> tuple[dict, list[dict]]:
    issues = []
    matches = list(SECTION.finditer(raw))
    question_text = raw[:matches[0].start()] if matches else raw
    answer_parts, analysis_parts = [], []
    answer_seen = 0
    for i, match in enumerate(matches):
        end = matches[i+1].start() if i+1 < len(matches) else len(raw)
        content = raw[match.end():end].strip()
        if match.group(1) == "答案":
            answer_seen += 1
            answer_parts.append(content)
        else:
            analysis_parts.append(match.group(0) + content)
    if answer_seen != 1:
        issues.append(issue("answer_marker_count", f"Expected one answer marker, found {answer_seen}"))
    fields = {"stem": question_text.strip(), "options": {}, "answer": "\n".join(answer_parts), "analysis": "\n".join(analysis_parts), "knowledge_point": ""}
    if kind == "choice":
        options = list(OPTION.finditer(question_text))
        # The source may mention e.g. circuit A、 inside the stem. A real option
        # list starts at a new paragraph/cell and has A/B/C/D in order.
        candidates = []
        for i, match in enumerate(options):
            if match.group(1) != "A" or question_text[question_text.rfind("\n", 0, match.start())+1:match.start()].strip():
                continue
            # Keep the source's option delimiter convention: mentions such as
            # 'A、B管' inside an A．/B． list are content, not new options.
            same_style = [m for m in options[i:] if m.group(0)[-1] == match.group(0)[-1]]
            if [m.group(1) for m in same_style] == list("ABCD"):
                candidates.append(same_style)
        if len(candidates) == 1:
            options = candidates[0]
        labels = [m.group(1) for m in options]
        if labels != ["A", "B", "C", "D"]:
            issues.append(issue("option_structure", f"Expected A/B/C/D exactly once in order; found {labels}"))
        if options:
            fields["stem"] = question_text[:options[0].start()].strip()
            for i, match in enumerate(options):
                end = options[i+1].start() if i+1 < len(options) else len(question_text)
                label = match.group(1)
                content = question_text[match.end():end].strip()
                if label in fields["options"]:
                    fields["options"][label] += "\n" + content
                else:
                    fields["options"][label] = content
        answer = fields["answer"].strip()
        if re.fullmatch("[A-D]", answer):
            fields["answer"] = answer
        else:
            issues.append(issue("choice_answer", "Single-choice answer must be exactly A, B, C or D; original answer preserved"))
    return fields, issues


def _questions(blocks: list[dict], kind: str, config: dict) -> tuple[list[dict], list[dict]]:
    questions, preamble = [], []
    current = None
    numbering_separator = None
    for block in blocks:
        text = block["text"]
        start = QUESTION_START.match(text)
        # Preserve explanation steps using a different numbering convention.
        # The first top-level question establishes this document's convention.
        if start and numbering_separator and start.group(2) != numbering_separator:
            start = None
        if start:
            numbering_separator = start.group(2)
            current = {"number": start.group(1), "raw_parts": [text[start.end():]], "block_indices": [block["index"]], "location": block.get("location", ""), "issues": []}
            questions.append(current)
        elif current:
            current["raw_parts"].append(text)
            current["block_indices"].append(block["index"])
        else:
            preamble.append(block)
        if current:
            for token in block["tokens"]:
                if token["kind"] == "unsupported":
                    current["issues"].append(issue("unsupported_content", token.get("reason", "Unsupported content"), token.get("location", "")))
    counts = {}
    for question in questions:
        counts[question["number"]] = counts.get(question["number"], 0) + 1
    for question in questions:
        raw = "\n".join(question.pop("raw_parts")).strip()
        fields, problems = _split_fields(raw, kind)
        question.update(fields)
        question["raw_text"] = raw
        question["issues"].extend(problems)
        question["id"] = f"q{question['number']}-{digest(raw.encode())[:12]}"
        if counts[question["number"]] > 1:
            question["issues"].append(issue("duplicate_number", "Duplicate source question number", question["location"]))
    return questions, preamble


def _render_questions(questions: list[dict], formulas: list[dict], assets: list[dict], config: dict):
    fmap, amap = {f["id"]: f for f in formulas}, {a["id"]: a for a in assets}
    for q in questions:
        refs = REF.findall(q["raw_text"])
        q["formula_ids"] = list(dict.fromkeys(r for r in refs if r.startswith("F")))
        image_ids = [fmap[r].get("fallback_asset_id") if r in fmap and fmap[r]["status"] == "image" else r for r in refs]
        q["asset_filenames"] = list(dict.fromkeys(amap[r]["filename"] for r in image_ids if r in amap))
        def render(text):
            def sub(match):
                ref = match.group(1)
                if ref in fmap:
                    f = fmap[ref]
                    if f["status"] == "converted" and f.get("latex"):
                        delim = config["formula_delimiter"]
                        return f"{delim}{f['latex']}{delim}"
                    if f["status"] == "image" and f.get("fallback_asset_id") in amap:
                        return config["image_reference"].format(filename=amap[f["fallback_asset_id"]]["filename"])
                    return f"【公式无法可靠保留 {ref}】"
                if ref in amap:
                    return config["image_reference"].format(filename=amap[ref]["filename"])
                return f"【引用待复核 {ref}】"
            return REF.sub(sub, text)
        for key in ("stem", "answer", "analysis"):
            q[key] = render(q[key])
        q["options"] = {key: render(value) for key, value in q["options"].items()}
        # Use formula/image CONTENT, not occurrence IDs, to keep difficulty stable.
        def canonical_ref(match):
            ref = match.group(1)
            if ref in fmap:
                f = fmap[ref]
                return f.get("latex") or f["source"]["sha256"]
            return amap[ref]["sha256"] if ref in amap else ref
        normalized = re.sub(r"\s+", "", REF.sub(canonical_ref, q["raw_text"]))
        h = digest((config["difficulty_seed"] + normalized).encode("utf-8"))
        q["difficulty"] = config["difficulty_levels"][int(h[:16], 16) % 3]


def validate_task(task: dict, task_dir: Path, source: Path | None = None) -> dict:
    if task.get("schema_version") != 1:
        raise ValueError("Unsupported task schema_version")
    if task.get("kind") not in ("choice", "blank"):
        raise ValueError("Unsupported question kind")
    config = task["config"]
    validate_config(config)
    if config_digest(config) != task.get("config_sha256"):
        raise ValueError("Configuration changed: parse again instead of applying stale task data")
    if digest(json.dumps(task["questions"], sort_keys=True, ensure_ascii=False).encode()) != task.get("questions_sha256"):
        raise ValueError("Question data changed outside the parser; rerun from the source document")
    if _metadata_digest(task) != task.get("metadata_sha256"):
        raise ValueError("Task metadata changed; parse again instead of modifying formula/resource status")
    global_issues = deepcopy(task.get("document_issues", []))
    resources = [task["source"]["snapshot"], *task["assets"]]
    for formula in task["formulas"]:
        resources.append(formula["source"])
        if formula.get("preview"):
            resources.append(formula["preview"])
    broken = set()
    for resource in resources:
        try:
            p = safe_file(task_dir, resource["path"])
            if digest(p.read_bytes()) != resource["sha256"]:
                raise ValueError("Content hash differs")
        except (OSError, ValueError) as exc:
            broken.add(resource["path"])
            global_issues.append(issue("resource_integrity", str(exc), resource["path"]))
    if source and digest(source.read_bytes()) != task["source"]["sha256"]:
        global_issues.append(issue("source_changed", "Source document changed; parse it as a new task", str(source)))
    fmap = {f["id"]: f for f in task["formulas"]}
    amap = {a["filename"]: a for a in task["assets"]}
    assets_by_id = {a["id"]: a for a in task["assets"]}
    validated = []
    for original in task["questions"]:
        q = deepcopy(original)
        for field in config["required_fields"][task["kind"]]:
            if not q.get(field):
                q["issues"].append(issue("required_field", f"Required field is empty: {field}", q["location"]))
        if task["kind"] == "choice":
            for label in "ABCD":
                if not q["options"].get(label, "").strip():
                    q["issues"].append(issue("empty_option", f"Missing or empty option {label}", q["location"]))
        for fid in q["formula_ids"]:
            f = fmap.get(fid)
            if f and f["status"] == "image":
                fallback = assets_by_id.get(f.get("fallback_asset_id"))
                preview = f.get("preview", {})
                if fallback and fallback.get('origin') == 'formula_rerender':
                    evidence = f.get('raster_evidence', {})
                    valid = (f.get('conversion_status') == 'converted'
                             and (f.get('output_policy') or {}).get('mode') == 'image'
                             and fallback.get('formula_id') == fid
                             and fallback.get('source_formula_sha256') == f['source']['sha256']
                             and f['source']['path'] not in broken
                             and fallback.get('latex_sha256') == digest((f.get('latex') or '').encode())
                             and evidence.get('latex_sha256') == fallback.get('latex_sha256')
                             and evidence.get('png_sha256') == fallback.get('sha256')
                             and evidence.get('renderer_resources_sha256') == fallback.get('renderer_resources_sha256')
                             and bool(evidence.get('renderer_resources_sha256'))
                             and evidence.get('engine') == fallback.get('engine')
                             and fallback['filename'] in q['asset_filenames'])
                    if not valid:
                        q['issues'].append(issue('formula_image_binding', 'Rerendered formula evidence differs', f['location'], formula_id=fid))
                elif (not fallback or fallback.get("origin") != "formula_preview"
                    or fallback.get("formula_id") != fid
                    or fallback.get("source_preview_sha256") != preview.get("sha256")
                    or not (f.get("preview_binding") or {}).get("verified")
                    or preview.get("path") in broken or f["source"]["path"] in broken
                    or fallback["filename"] not in q["asset_filenames"]):
                    q["issues"].append(issue("formula_image_binding", "Formula image has missing or inconsistent original-preview binding", f["location"], formula_id=fid))
            elif not f or f["status"] != "converted" or not f.get("latex"):
                reason = f.get("reason", "Unresolved formula") if f else "Missing formula"
                if f and f.get("fallback_error"):
                    reason += "; image fallback: " + f["fallback_error"]
                q["issues"].append(issue("formula_review", reason, f.get("location", "") if f else q["location"], formula_id=fid))
            elif f["source"]["path"] in broken:
                q["issues"].append(issue("formula_evidence", "Formula evidence changed or missing", f["location"], formula_id=fid))
        for filename in q["asset_filenames"]:
            a = amap.get(filename)
            if not a or a["path"] in broken:
                q["issues"].append(issue("image_missing", f"Missing or changed image {filename}", q["location"]))
            elif a["extension"] not in config["supported_image_extensions"]:
                q["issues"].append(issue("image_format", f"Image format needs review: {filename}", a["location"]))
            elif a.get("review_reason"):
                q["issues"].append(issue("image_format", a["review_reason"], a["location"]))
        if not q["issues"]:
            # A single unexportable row must be quarantined, not make the
            # unattended run discard other sound questions.
            from .exporter import validate_export_question
            try:
                validate_export_question(q, task["kind"], config)
            except ValueError as exc:
                q["issues"].append(issue("export_content", str(exc), q["location"]))
        q["status"] = "review" if q["issues"] else "passed"
        validated.append(q)
    passed = sum(q["status"] == "passed" for q in validated)
    return {"schema_version": 1, "source": task["source"]["name"], "kind": task["kind"], "contract_status": "unverified", "total": len(validated), "passed": passed, "review": len(validated)-passed, "document_issues": global_issues, "questions": validated, "formulas": formula_counts(task["formulas"]),
            "passed_with_formula_images": sum(q["status"] == "passed" and any(fmap[fid]["status"] == "image" for fid in q["formula_ids"]) for q in validated)}


def formula_counts(formulas: list[dict]) -> dict:
    return {"total": len(formulas), "converted": sum(f["status"] == "converted" for f in formulas),
            "image": sum(f["status"] == "image" for f in formulas),
            "review": sum(f["status"] not in ("converted", "image") for f in formulas)}


def _metadata_digest(task: dict) -> str:
    fields = {k: task[k] for k in ("kind", "source", "blocks", "formulas", "assets", "document_issues")}
    return digest(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode())


def parse_document(source: Path, kind: str, out_dir: Path, config: dict | None = None) -> dict:
    from .document import read_document

    if kind not in ("choice", "blank"):
        raise ValueError("kind must be choice or blank")
    if source.suffix.lower() != ".docx":
        raise ValueError("Only .docx input is supported")
    if out_dir.exists():
        raise FileExistsError(f"Output already exists: {out_dir}")
    config = deepcopy(config or load_config())
    validate_config(config)
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".qa-parse-", dir=out_dir.parent))
    try:
        if source.stat().st_size > 128 * 1024 * 1024:
            raise ValueError("DOCX exceeds compressed size limit")
        data = source.read_bytes()
        snapshot = _save_bytes(staging, "source/input.docx", data)
        document = read_document(staging / snapshot["path"])
        blocks, formulas, assets = _prepare_blocks(document, staging, config)
        questions, preamble = _questions(blocks, kind, config)
        from .mixed_formulas import apply_mixed_formulas
        apply_mixed_formulas(questions, formulas, assets, blocks, staging, config)
        _render_questions(questions, formulas, assets, config)
        document_issues = []
        for value in document.get("issues", []):
            problem = issue(value.get("code", "document"), value.get("message", value.get("reason", str(value))), value.get("location", ""))
            # Local unsupported content must not block explicitly requested partial exports.
            owner = next((q for q in questions if any(problem["location"].startswith(b["location"] + "/") or problem["location"] == b["location"] for b in blocks if b["index"] in q["block_indices"])), None) if problem["location"] else None
            if owner is not None:
                if problem not in owner["issues"]:
                    owner["issues"].append(problem)
            else:
                document_issues.append(problem)
        if not questions:
            document_issues.append(issue("no_questions", "No numbered questions detected"))
        heading = "\n".join(b["text"] for b in preamble)
        if (kind == "choice" and "填空题" in heading) or (kind == "blank" and "单选题" in heading):
            document_issues.append(issue("kind_mismatch", "Selected kind conflicts with source heading"))
        if "多选题" in heading:
            document_issues.append(issue("unsupported_kind", "Multiple-choice documents require review"))
        task = {"schema_version": 1, "parser_version": "0.2.0", "kind": kind, "source": {"name": source.name, "original_path": str(source.resolve()), "sha256": digest(data), "snapshot": snapshot}, "config": config, "config_sha256": config_digest(config), "blocks": blocks, "preamble_block_indices": [b["index"] for b in preamble], "questions": questions, "formulas": formulas, "assets": assets, "document_issues": document_issues}
        task["questions_sha256"] = digest(json.dumps(questions, sort_keys=True, ensure_ascii=False).encode())
        task["metadata_sha256"] = _metadata_digest(task)
        write_json(staging / "task.json", task)
        report = validate_task(task, staging)
        write_json(staging / "report.json", report)
        # Reserve without replacing an existing directory; task.json is the final
        # completion marker. A crash can leave an incomplete directory, never a
        # falsely complete task. A normal failure removes only our own directory.
        out_dir.mkdir()
        try:
            for child in list(staging.iterdir()):
                if child.name != "task.json":
                    child.rename(out_dir / child.name)
            (staging / "task.json").rename(out_dir / "task.json")
            staging.rmdir()
        except BaseException:
            shutil.rmtree(out_dir, ignore_errors=True)
            raise
        return report
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def load_task(path: Path) -> tuple[dict, Path]:
    path = path / "task.json" if path.is_dir() else path
    return json.loads(path.read_text(encoding="utf-8")), path.parent


def export_document(task_path: Path, out_dir: Path, allow_partial: bool = False) -> dict:
    from .exporter import export_task

    task, root = load_task(task_path)
    report = validate_task(task, root)
    if report["document_issues"]:
        raise ValueError("Document-level issues must be resolved before exporting: " + json.dumps(report["document_issues"], ensure_ascii=False))
    export_input = deepcopy(task)
    export_input["questions"] = report["questions"]
    return export_task(export_input, root, out_dir, allow_partial=allow_partial)


def run_document(source: Path, kind: str, out_dir: Path, config: dict | None = None) -> dict:
    """Unattended conversion: deliver successes plus explicit quarantine evidence.

    A completed run with exclusions is not whole-document acceptance. summary.json
    is published last. No prompt, manual correction or implicit omission occurs.
    """
    if out_dir.exists() or out_dir.is_symlink():
        raise FileExistsError(f"Output already exists: {out_dir}")
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".qa-run-", dir=out_dir.parent))
    report, task, export_report = None, None, None
    try:
        try:
            report = parse_document(source, kind, staging / "task", config)
            task, root = load_task(staging / "task")
            # Unknown scope/global faults quarantine the whole document.
            if report["document_issues"]:
                quarantined = deepcopy(report["questions"])
                for q in quarantined:
                    q["issues"].extend(deepcopy(report["document_issues"]))
                included = []
                status = "document_quarantined"
            else:
                quarantined = [q for q in report["questions"] if q["issues"]]
                included = [q for q in report["questions"] if not q["issues"]]
                status = "completed_with_quarantine" if quarantined else "completed"
            if included:
                export_report = export_document(root, staging / "export", allow_partial=True)
            else:
                (staging / "export").mkdir()
                export_report = {"schema_version": 1, "exported_questions": 0, "partial": True,
                                 "omitted_questions": [{"number": q["number"], "issues": q["issues"]} for q in quarantined],
                                 "document_issues": report["document_issues"], "import_contract": "unverified",
                                 "notes": ["No safe rows: no questions.xlsx has been created."]}
                write_json(staging / "export/report.json", export_report)
            quarantine = {"schema_version": 1, "source": source.name,
                          "document_issues": report["document_issues"], "total_quarantined": len(quarantined),
                          "questions": [{key: q[key] for key in ("id", "number", "location", "issues", "formula_ids")} for q in quarantined]}
            exported_formula_ids = {fid for q in included for fid in q["formula_ids"]}
            exported_formulas = [f for f in task["formulas"] if f["id"] in exported_formula_ids]
            summary = {"schema_version": 1, "status": status, "source": source.name,
                       "total": report["total"], "passed": len(included), "quarantined": len(quarantined),
                       "formulas": report["formulas"], "exported_formulas": formula_counts(exported_formulas),
                       "questions_with_formula_images": sum(any(f["status"] == "image" and f["id"] in q["formula_ids"] for f in exported_formulas) for q in included),
                       "document_issues": report["document_issues"],
                       "acceptance": {"target_system_import": "unverified", "target_formula_rendering": "unverified", "windows": "unverified"},
                       "files": {"task": "task/task.json", "export": "export", "quarantine": "quarantine.json"}}
        except Exception as exc:
            # Preserve any parsed task and evidence; finish with a clear failure
            # report, never present an exception as complete or empty success.
            problem = issue("run_failure", f"{type(exc).__name__}: {exc}", str(source))
            questions = report["questions"] if report else []
            quarantine = {"schema_version": 1, "source": source.name, "document_issues": [problem],
                          "total_quarantined": len(questions),
                          "questions": [{"id": q["id"], "number": q["number"], "location": q["location"],
                                         "formula_ids": q["formula_ids"], "issues": [*q["issues"], problem]} for q in questions]}
            summary = {"schema_version": 1, "status": "failed", "source": source.name,
                       "total": len(questions) if report else None, "passed": 0, "quarantined": len(questions) if report else None,
                       "document_issues": [problem], "formulas": report["formulas"] if report else formula_counts([]),
                       "exported_formulas": formula_counts([]),
                       "acceptance": {"target_system_import": "unverified", "windows": "unverified"}}
            # Keep malformed but readable input for an automatic failure bundle.
            # Never exceed the same size guard used for parsing.
            try:
                if source.is_file() and source.stat().st_size <= 128 * 1024 * 1024:
                    summary["source_evidence"] = _save_bytes(staging, "failed-source/input.docx", source.read_bytes())
            except OSError as evidence_error:
                summary["source_evidence_error"] = str(evidence_error)
        from .result import user_result, result_text, enrich_problem_reasons
        summary["user_result"] = user_result(summary, quarantine)
        if summary.get("passed") and (staging / "export/questions.xlsx").is_file():
            try:
                from .export_preview import render_export_preview
                cfg = task["config"]
                html, display_report = render_export_preview(staging / "export", source.name,
                    formula_delimiter=cfg["formula_delimiter"], image_reference=cfg["image_reference"])
                summary["export_preview"] = _save_bytes(staging, "export/题目预览.html", html.encode("utf-8"))
                font_root = Path(__file__).parent / "web/vendor/fonts"
                summary["display_assets"] = [_save_bytes(staging, "_display/" + name, (font_root / name).read_bytes())
                    for name in ("NotoSansCJKsc-Regular.otf", "OFL.txt")]
                summary["display_report"] = display_report
            except Exception as preview_error:
                summary["export_preview_error"] = f"{type(preview_error).__name__}: {preview_error}"
                summary["user_result"]["preview_notice"] = "题目预览生成失败，Excel 和诊断记录已保留。"
        if quarantine.get("questions") and (staging / "task/task.json").is_file():
            try:
                from .problem_preview import build_problem_preview, render_preview_html
                preview = build_problem_preview(staging / "task", source.name)
                enrich_problem_reasons(summary["user_result"], preview)
                if preview.get("questions"):
                    html = render_preview_html(preview).encode("utf-8")
                    summary["problem_preview"] = _save_bytes(staging, "未导出题目.html", html)
            except Exception as preview_error:
                # Explanation is auxiliary: never change the parser/export decision.
                summary["problem_preview_error"] = f"{type(preview_error).__name__}: {preview_error}"
                summary["user_result"]["preview_notice"] = "问题预览未能生成；转换结果及原始诊断记录已保留。"
        (staging / "result.txt").write_text(result_text(summary["user_result"]), encoding="utf-8")
        write_json(staging / "quarantine.json", quarantine)
        write_json(staging / "summary.json", summary)
        out_dir.mkdir()
        try:
            for child in list(staging.iterdir()):
                if child.name != "summary.json":
                    child.rename(out_dir / child.name)
            (staging / "summary.json").rename(out_dir / "summary.json")
            staging.rmdir()
        except BaseException:
            shutil.rmtree(out_dir, ignore_errors=True)
            raise
        return summary
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
