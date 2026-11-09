"""Explainable per-question classification after organization segmentation."""
from pathlib import Path
from .document import read_document
from .question_structure import build_questions

RULES_VERSION = 2


def _plain_blocks(document: dict) -> list[dict]:
    blocks = []
    for block in document["blocks"]:
        text = []
        for token in block["tokens"]:
            if token["kind"] == "text":
                value = token["text"]
                if token.get("underline") and value and not value.strip():
                    value = "_" * max(3, len(value))
                text.append(value)
            elif token["kind"] in ("formula", "image"):
                text.append("［文档对象］")
            elif token["kind"] == "unsupported":
                text.append("［未识别内容］")
        blocks.append({**block, "text": "".join(text)})
    return blocks


def classify_document(path: Path) -> dict:
    """Unknown children do not prevent sound siblings from being converted."""
    result = {"rules_version": RULES_VERSION, "status": "unknown", "kind": None,
              "reason": "无法可靠判断文档题型，未进行转换。", "total": 0,
              "counts": {"choice": 0, "blank": 0, "unknown": 0}, "questions": []}
    try:
        blocks = _plain_blocks(read_document(Path(path)))
        questions, _, structure = build_questions(blocks, "auto", {})
    except Exception as exc:
        result.update(status="failed", reason="无法读取这份 Word，请检查文件是否完整。",
                      diagnostic=f"{type(exc).__name__}: {exc}")
        return result
    result["total"] = len(questions)
    result["structure"] = structure
    for q in questions:
        kind = q["kind"]
        result["counts"][kind if kind in ("choice", "blank") else "unknown"] += 1
        result["questions"].append({"id": q["id"], "number": q["number"], "kind": kind,
            "parent_id": q["parent_id"], "number_path": q["number_path"],
            "organization": q["organization"], "location": q["location"],
            "evidence": q["classification_evidence"]})
    counts = result["counts"]
    kinds = [k for k in ("choice", "blank") if counts[k]]
    if not questions:
        result["reason"] = "未找到可识别的题目，未进行转换。"
    elif len(kinds) == 2:
        result.update(status="mixed", reason="含选择题和填空题，按每道题的类型处理；无法确认的题目单独报告。")
    elif len(kinds) == 1:
        kind = kinds[0]
        result.update(status="partial" if counts["unknown"] else "classified", kind=kind,
            reason=("已识别部分题型；未知或未支持题目单独报告。" if counts["unknown"] else
                    f"根据正文 {len(questions)} 道题的结构识别为" + ("选择题。" if kind == "choice" else "填空题。")))
    else:
        result["reason"] = "所有题目均需复核或属于暂未支持的作答方式。"
    return result
