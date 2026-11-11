"""Classify the role of prompt tables without treating every table as a failure.

This layer never removes source text. Interactive cells remain in the task and
are excluded by an explicit, question-scoped policy issue. Formula tokens are
opaque: a formula's underscores are not Word answer slots.
"""
from __future__ import annotations

from collections import defaultdict
import re


_SECTION = re.compile(r"【(?:答案|详解|解析|分析|点睛|点精)】")
_BLANK = re.compile(r"(?<![A-Za-z0-9_])(?:_{2,}(?:\d+_{2,})?|[＿﹍]{2,})(?![A-Za-z_])|(?<=[A-Za-z])_{3,}(?![A-Za-z_])|(?<=[（(])[ \t\u00a0\u3000]+(?=[）)])")
_LABEL = re.compile(r"([①-⑳㉑-㉟㊱-㊿])\s*$")
_OPTION = re.compile(r"^\s*[A-H][．.、]")
_MATH = re.compile(r"\$\$[\s\S]*?\$\$|\\\([\s\S]*?\\\)|⟦[FI]\d{6}⟧")
_TABLE_INSTRUCTION = re.compile(r"(?:填写|填入|补全|完成)[^。！？\n]{0,16}(?:下表|表格|表中)|(?:下表|表格|表中)[^。！？\n]{0,16}(?:填写|填入|补全)")


def _token_text(token: dict) -> str:
    if token.get("kind") == "text":
        value = token.get("text", "")
        if token.get("underline") and value and not value.strip():
            return "_" * max(3, len(value))
        return value
    if token.get("kind") in ("formula", "image"):
        return f"⟦{token['id']}⟧" if token.get("id") else "［文档对象］"
    return "【内容待复核】"


def _view(block: dict) -> tuple[str, list[dict]]:
    """Build offset-preserving text and token provenance, matching core render."""
    tokens = block.get("tokens", [])
    if not tokens:
        return block.get("text", ""), []
    pieces, spans, offset = [], [], 0
    for index, token in enumerate(tokens):
        value = _token_text(token)
        pieces.append(value)
        spans.append({"token_index": index, "start": offset, "end": offset + len(value),
                      "location": token.get("location", block.get("location", ""))})
        offset += len(value)
    return "".join(pieces), spans


def _slots(block: dict) -> list[dict]:
    text, spans = _view(block)
    # Preserve offsets, but never identify delimiters inside literal math.
    # Letter-labelled Word blanks such as a________ are valid interactions;
    # unlike a formula token, their underscore run is source paragraph text.
    searchable = _MATH.sub(lambda m: "¤" * len(m.group()), text)
    found = []
    for match in _BLANK.finditer(searchable):
        label = _LABEL.search(searchable[:match.start()])
        overlaps = [s for s in spans if s["start"] < match.end() and s["end"] > match.start()]
        found.append({"block_index": block["index"], "location": overlaps[0]["location"] if overlaps else block.get("location", ""),
                      "start": match.start(), "end": match.end(), "label": label.group(1) if label else None})
    return found


def _question_blocks(question: dict, blocks: list[dict]) -> list[dict]:
    indices = set(question.get("block_indices", []))
    # Material references are block indices. Accept explicit dictionary refs as
    # well, so a caller may carry a stable material ID alongside its location.
    for ref in question.get("material_refs", []):
        if isinstance(ref, int):
            indices.add(ref)
        elif isinstance(ref, dict):
            indices.update(ref.get("block_indices", []))
            if isinstance(ref.get("block_index"), int):
                indices.add(ref["block_index"])
    selected = [b for b in blocks if b.get("index") in indices]
    prompt = []
    for block in sorted(selected, key=lambda b: b["index"]):
        # Tables explaining the answer are content, not answer interactions.
        # Partial marker paragraphs are kept only before their answer section.
        text = block.get("text", _view(block)[0])
        marker = _SECTION.search(text)
        if marker:
            break
        prompt.append(block)
    return prompt


def apply_table_policy(question: dict, blocks: list[dict], config: dict) -> None:
    """Add role decisions, slot aliases and local issues to one question.

    ``table_slot_aliases`` only describes a circled slot that occurs exactly
    once in the table and exactly once in the body's explicit answer area.
    Equal labels elsewhere do not prove equal slots. Role evidence and source
    spans are retained even when the supported policy skips interaction.
    """
    prompt = _question_blocks(question, blocks)
    tables = defaultdict(list)
    for block in prompt:
        if block.get("kind") == "table_row":
            tables[block.get("table_location") or block.get("location", "")].append(block)
    question["table_roles"] = []
    question["table_slot_aliases"] = []
    issues = question.setdefault("issues", [])
    # Support repeat validation without appending duplicate policy failures.
    issues[:] = [i for i in issues if i.get("origin") != "table_policy"]
    body_slots = defaultdict(list)
    for block in prompt:
        if block.get("kind") != "table_row":
            text = _view(block)[0]
            slots = _slots(block)
            # Alias only a dedicated list such as ①____②____, not two different
            # prose questions that happen to restart their circled numbering.
            remainder = _BLANK.sub("", text)
            remainder = re.sub(r"[①-⑳㉑-㉟㊱-㊿\s、，,；;：:]", "", remainder)
            if not remainder:
                for slot in slots:
                    if slot["label"]:
                        body_slots[slot["label"]].append(slot)
    all_table_slots = defaultdict(list)
    explicit_table_task = bool(_TABLE_INSTRUCTION.search("\n".join(_view(b)[0] for b in prompt if b.get("kind") != "table_row")))
    for table_location, rows in tables.items():
        slots, cell_views, incomplete_metadata = [], [], False
        for row in rows:
            row_slots = _slots(row)
            slots.extend(row_slots)
            tokens = row.get("tokens", [])
            cells = row.get("cells", [])
            if not cells or not tokens:
                incomplete_metadata = True
            for cell in cells:
                lo, hi = cell.get("token_start"), cell.get("token_end")
                if not isinstance(lo, int) or not isinstance(hi, int) or not (0 <= lo <= hi <= len(tokens)):
                    incomplete_metadata = True
                    continue
                cell_text = "".join(_token_text(t) for t in tokens[lo:hi])
                cell_views.append(cell_text)
                properties = cell.get("properties_xml") or ""
                # Empty cells are normal in static tables and in merged rows.
                # Only an explicit table-filling instruction establishes a
                # blank-cell interaction; merged-cell continuations do not.
                if explicit_table_task and not cell_text.strip() and "vMerge" not in properties and "hMerge" not in properties:
                    _, token_spans = _view(row)
                    start = token_spans[lo]["start"] if lo < len(token_spans) else len(_view(row)[0])
                    slots.append({"block_index": row["index"], "location": cell.get("location", row.get("location", "")),
                                  "start": start, "end": start, "label": None, "empty_cell": True})
        for slot in slots:
            if slot["label"]:
                all_table_slots[slot["label"]].append(slot)
        spans = [{"block_index": r["index"], "location": r.get("location", ""),
                  "row": r.get("row"), "cells": [{"index": c.get("index"), "location": c.get("location", "")}
                                                   for c in r.get("cells", [])]} for r in rows]
        evidence = []
        if question.get("kind") == "choice" and cell_views and any(_OPTION.match(c) for c in cell_views):
            role = "layout"
            evidence.append("单元格承载选择题选项，不属于表内填空作答")
        elif slots and question.get("kind") != "choice":
            if incomplete_metadata:
                role = "unknown"
                evidence.append("存在疑似空位，但单元格来源范围不完整，不能确定交互位置")
                issues.append({"code": "table_role_unknown", "message": evidence[-1], "location": table_location,
                               "origin": "table_policy"})
            else:
                # A header is ordinary static content. Additional complete data
                # rows alongside interaction rows indicate a mixed-role table.
                body_rows = rows[1:] if len(rows) > 1 else rows
                interaction_rows = {s["block_index"] for s in slots}
                role = "mixed" if any(r["index"] not in interaction_rows for r in body_rows) else "answer_interaction"
                evidence.append("表格单元格包含明确的下划线空位或带编号空位")
                issues.append({"code": "table_interaction_skipped", "message": "按策略跳过需要在表格内填空作答的题目；原表及空位证据已保留",
                               "location": table_location, "origin": "table_policy", "policy_skip": True,
                               "affected_block_indices": sorted({s["block_index"] for s in slots}),
                               "source_spans": slots})
        else:
            role = "static_material"
            evidence.append("表格提供已给定内容，未发现表内填空交互；保留为题目材料")
        question["table_roles"].append({"table_location": table_location, "role": role,
                                        "evidence": evidence, "source_spans": spans, "slots": slots})
    for label, occurrences in all_table_slots.items():
        if len(occurrences) == 1 and len(body_slots[label]) == 1:
            question["table_slot_aliases"].append({"label": label, "binding": "same_explicit_label",
                                                  "table_occurrences": occurrences, "body_occurrences": body_slots[label]})
