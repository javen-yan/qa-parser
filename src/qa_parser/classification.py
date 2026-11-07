"""Explainable, conservative question-type detection from document body only.

Rules v1 use the same numbered-question/answer segmentation as the parser.
Matching numbered subquestions and numbered answers establish a fill-in
large question even when a subquestion contains choices. Otherwise a complete
ordered A/B/C/D option list is choice evidence (invalid/missing answers go to
normal per-question validation). A blank marker or fill-in instruction plus
an answer and no option-list signal establishes a simple fill-in question.
Multiple blanks with multiple distinct answer fields also establish a fill-in
large question; ordinary choice-answer prose does not. Generic requests to
explain/answer or numbered essay parts are not fill-in evidence. Headings and
filenames never establish the type alone.
Every numbered question must have evidence and the whole document must agree;
otherwise automatic conversion is refused rather than guessing a template.
Formula/image bytes are represented by neutral placeholders, not OCR'd.
"""
from __future__ import annotations

from pathlib import Path
import re

from .core import _questions, _split_fields
from .document import read_document

RULES_VERSION = 1
BLANK = re.compile(r"_{2,}|[＿﹍]{2,}|填空|填入|填写")
SUBPART = re.compile(r"(?:^|\n)\s*[（(]([1-9]\d*)[）)]")
OPTION_LINE = re.compile(r"(?:^|\n|\t)\s*([A-H])[．.、]")
UNSUPPORTED_HEADING = re.compile(r"^\s*(?:[一二三四五六七八九十]+[、．. ]\s*)?(?:判断题|判断正误|简答题|论述题|作文|证明题)\s*$")


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
    """Return JSON-safe classification/evidence; malformed inputs are failures."""
    result = {"rules_version": RULES_VERSION, "status": "unknown", "kind": None,
              "reason": "无法可靠判断文档题型，未进行转换。", "total": 0,
              "counts": {"choice": 0, "blank": 0, "unknown": 0}, "questions": []}
    try:
        document = read_document(Path(path))
        blocks = _plain_blocks(document)
        questions, _ = _questions(blocks, "blank", {})
    except Exception as exc:
        result.update(status="failed", reason="无法读取这份 Word，请检查文件是否完整。",
                      diagnostic=f"{type(exc).__name__}: {exc}")
        return result
    result["total"] = len(questions)
    for question in questions:
        choice_fields, issues = _split_fields(question["raw_text"], "choice")
        complete_options = not any(i["code"] == "option_structure" for i in issues)
        labels = [match.group(1) for match in OPTION_LINE.finditer(question["stem"])]
        # Even incomplete option lists must not be mistaken for a fill-in task.
        option_signal = len(set(labels)) >= 2
        has_answer = bool(question["answer"].strip()) and not any(
            i["code"] == "answer_marker_count" for i in question["issues"])
        fill_signal = bool(BLANK.search(question["stem"]))
        evidence = []
        stem_parts = set(SUBPART.findall(question["stem"]))
        answer_parts = set(SUBPART.findall(question["answer"]))
        multipart = len(stem_parts & answer_parts) >= 2
        blank_slots = len(re.findall(r"_{2,}|[＿﹍]{2,}", question["stem"]))
        descriptive_answer = bool(question["answer"].strip()) and not re.fullmatch(r"[A-H\s、,，.．;；]+", question["answer"].strip())
        answer_fields = [part for part in re.split(r"\s{2,}|\n", question["answer"].strip()) if part.strip()]
        choice_answer_hint = re.fullmatch(
            r"(?:答案(?:为|是|[:：])?|应?选(?:择)?|故选)?[A-H](?:项|选项)?[。.]?",
            re.sub(r"\s+", "", question["answer"]))
        multiple_fills = (blank_slots >= 2 and descriptive_answer and not choice_answer_hint
                          and (not complete_options or len(answer_fields) >= 2))
        if (multipart or multiple_fills) and fill_signal and has_answer:
            kind = "blank"
            evidence.append("公共题干含多个编号小问，答案含对应编号" if multipart else "题干含多个填空位置，答案包含填入内容而非仅选项字母")
            evidence.append("小问中的选项保留在同一道填空大题内")
        elif complete_options:
            kind = "choice"
            evidence.append("题干后有按顺序排列的 A、B、C、D 选项")
            if re.fullmatch(r"[A-D]", choice_fields["answer"].strip()):
                evidence.append("答案是对应的单个选项字母")
        elif fill_signal and not option_signal and has_answer:
            kind = "blank"
            evidence.append("题干有明确的填空标记或填空指令")
            evidence.append("有独立答案且未发现选择题选项列表")
        else:
            kind = "unknown"
            evidence.append("题目结构不足以可靠区分选择题与填空题")
            if option_signal:
                evidence.append("发现不完整或不规范的选项列表")
            if not has_answer:
                evidence.append("答案缺失或无法准确归属")
        result["counts"][kind] += 1
        result["questions"].append({"number": question["number"], "kind": kind,
                                    "location": question["location"], "evidence": evidence})
    counts = result["counts"]
    if any(UNSUPPORTED_HEADING.fullmatch(block["text"]) for block in blocks):
        result.update(reason="文档包含目前不支持的题型，未进行转换。", unsupported_heading=True)
    elif counts["choice"] and counts["blank"]:
        result.update(status="mixed", reason="同一份 Word 内含选择题和填空题，目前不支持混合转换。")
    elif not questions:
        result["reason"] = "未找到可识别的题号和题目，未进行转换。"
    elif counts["unknown"]:
        result["reason"] = "部分题目无法可靠判断题型，未进行转换。"
    else:
        kind = "choice" if counts["choice"] else "blank"
        result.update(status="classified", kind=kind,
                      reason=f"根据正文 {len(questions)} 道题的结构识别为" + ("选择题。" if kind == "choice" else "填空题。"))
    return result
