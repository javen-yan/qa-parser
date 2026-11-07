"""Customer-facing results; engine diagnostics stay in JSON reports."""
REASONS = {
    "formula_review": "部分公式无法完整保留",
    "word_script": "部分上下标无法准确对应",
    "formula_image_binding": "公式与图片无法可靠对应",
    "formula_evidence": "公式资源缺失或发生变化",
    "option_structure": "选项结构不完整或无法准确区分",
    "empty_option": "缺少选项内容",
    "choice_answer": "答案无法与单选题选项准确对应",
    "answer_marker_count": "答案缺失或无法准确归属",
    "required_field": "缺少题目必要内容",
    "duplicate_number": "题号重复，无法准确归属",
    "image_missing": "题目图片缺失或发生变化",
    "image_format": "部分图片无法可靠读取",
    "resource_integrity": "文档资源缺失或发生变化",
    "export_content": "题目内容无法完整写入导出文件",
    "unsupported_content": "部分文档内容暂不支持",
    "kind_mismatch": "所选题型与文档内容不一致",
    "unsupported_kind": "文档包含暂不支持的题型",
    "no_questions": "未找到可识别的题目",
    "source_changed": "输入文档发生变化",
    "run_failure": "文档无法读取或处理",
}


def user_result(summary: dict, quarantine: dict) -> dict:
    passed = summary.get("passed") or 0
    total = summary.get("total")
    complete = summary.get("status") == "completed" and total == passed and passed > 0
    status = "success" if complete else "partial_failure" if passed else "failure"
    if status == "success":
        message = f"转换成功：已导出全部 {passed} 题。"
    elif status == "partial_failure":
        message = f"部分失败：已导出 {passed}/{total} 题，{total-passed} 题未导出。结果不完整。"
    else:
        message = "转换失败：未生成可用的题目导出文件。"
    affected = []
    for q in quarantine.get("questions", []):
        reasons = list(dict.fromkeys(REASONS.get(i["code"], "部分内容无法可靠处理") for i in q["issues"]))
        affected.append({"id": q.get("id"), "number": q["number"], "reasons": reasons})
    reasons = list(dict.fromkeys(
        [REASONS.get(i["code"], "文档无法可靠处理") for i in quarantine.get("document_issues", [])]
        + [reason for q in affected for reason in q["reasons"]]))
    return {"status": status, "message": message, "reasons": reasons,
            "affected_questions": affected, "report": "result.txt",
            "diagnostics": "quarantine.json", "export": "export" if passed else None}


def result_text(result: dict, brief: bool = False) -> str:
    lines = [result["message"]]
    if result["reasons"]:
        lines.append("原因：" + "；".join(result["reasons"]) + "。")
    affected = result["affected_questions"]
    if brief and affected:
        numbers = "、".join(str(q["number"]) for q in affected[:20])
        lines.append("未导出题号：" + numbers + (f"等，共 {len(affected)} 题，完整名单见结果说明。" if len(affected) > 20 else "。"))
    elif affected:
        lines.append("未导出题目：")
        lines.extend(f"第{q['number']}题：{'；'.join(q['reasons'])}" for q in affected)
    if result.get("preview_notice"):
        lines.append(result["preview_notice"])
    if result["export"]:
        lines.append("已保留通过检查的 Excel 和图片。")
    lines.append("目标系统导入：未验证。")
    return "\n".join(lines) + "\n"


def enrich_problem_reasons(result: dict, preview: dict) -> None:
    """Attach verified field-level explanations without changing conversion status."""
    by_id = {q["id"]: q for q in preview.get("questions", [])}
    for affected in result["affected_questions"]:
        question = by_id.get(affected.get("id"))
        if not question:
            continue
        details = [{key: problem.get(key, "") for key in
                    ("id", "field_label", "label", "reason", "impact", "source_label")}
                   for problem in question.get("problems", [])]
        if details:
            affected["details"] = details
            affected["reasons"] = [f"{p['field_label']}：{p['reason']} {p['impact']}" for p in details]
