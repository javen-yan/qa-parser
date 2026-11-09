"""Evidence-based blank binding; no solving, punctuation splitting or rewriting math.

The parser calls this before rendering object references.  Offsets always refer
to the original stem/answer, so the completed statement remains auditable.
"""
from __future__ import annotations

import re

VERSION = 1
BLANK = re.compile(r"_{2,}(?:[0-9]+_{2,})?|[＿﹍]{2,}|(?<=[（(])[ \t\u00a0\u3000]+(?=[）)])")
SUBPART = re.compile(r"(?:^|\n)[ \t]*(?:⟦[FI]\d{6}⟧[ \t]*)*[（(]([1-9]\d*)[）)]")
ANSWER_PART = re.compile(r"(?:^|\n|(?<=[ \t])[ \t]+)[ \t]*[（(]([1-9]\d*)[）)]")
FIELD_BOUNDARY = re.compile(r"(?:[^\S\r\n]{2,}|\r?\n)+")
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def _masked(text: str) -> str:
    # The normal pipeline uses opaque references; also protect already-rendered
    # formula syntax when inspecting a persisted question in diagnostics/tests.
    return re.sub(r"\$\$.*?\$\$|⟦[FI]\d{6}⟧", lambda m: "¤" * len(m[0]), text, flags=re.S)


def _problem(q: dict, message: str, **extra) -> None:
    q.setdefault("issues", []).append({"code": "blank_answer_alignment", "message": message,
                                       "location": q.get("location", ""), **extra})


def _source_spans(q: dict, blocks: list[dict], stem: str) -> list[dict]:
    """Map retained block text into the composed stem, preserving source order."""
    result, cursor = [], 0
    owned = set(q.get("block_indices", []))
    for block in blocks:
        if block.get("index") not in owned:
            continue
        text = block.get("text", "")
        if not text:
            continue
        start = stem.find(text, cursor)
        offset = 0
        if start < 0:
            # Top-level number is removed by structure parsing, not source IO.
            prefix = re.match(r"^\s*\d{1,5}(?:[．、]|\.(?!\d))\s*", text)
            if prefix:
                offset = prefix.end()
                text = text[offset:]
                start = stem.find(text, cursor)
        if start >= 0 and text:
            result.append({"start": start, "end": start + len(text), "block_index": block["index"],
                           "location": block.get("location", ""), "block_text_offset": offset,
                           "table_location": block.get("table_location")})
            cursor = start + len(text)
    return result


def _fields(text: str, expected: int, offset: int = 0) -> list[dict] | None:
    """Only Word layout boundaries separate unlabelled fields; punctuation doesn't."""
    left = len(text) - len(text.lstrip())
    value = text.strip()
    if not value:
        return [] if expected == 0 else None
    if expected == 1:
        return [{"text": value, "start": offset + left, "end": offset + left + len(value)}]
    result, cursor = [], 0
    for boundary in list(FIELD_BOUNDARY.finditer(text)) + [None]:
        end = boundary.start() if boundary else len(text)
        part = text[cursor:end]
        if part.strip():
            trim = len(part) - len(part.lstrip())
            result.append({"text": part.strip(), "start": offset + cursor + trim,
                           "end": offset + end - (len(part) - len(part.rstrip()))})
        cursor = boundary.end() if boundary else len(text)
    return result if len(result) == expected else None


def _labelled_answers(answer: str, labels: list[str]) -> dict[str, dict] | None:
    """Explicit numbered blanks such as __76__ have matching numbered answers."""
    if not labels or any(not label for label in labels) or len(labels) != len(set(labels)):
        return None
    if all(label.isdigit() for label in labels):
        pattern = re.compile(r"(?<!\S)(\d+)[．.、]")
    elif all(label in CIRCLED for label in labels):
        pattern = re.compile("([" + CIRCLED + "])")
    else:
        return None
    matches = list(pattern.finditer(answer))
    if [m[1] for m in matches] != labels or answer[:matches[0].start()].strip():
        return None
    result = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(answer)
        fields = _fields(answer[match.end():end], 1, match.end())
        if not fields:
            return None
        result[match[1]] = fields[0]
    return result


def _answer_view(answer: str, fmap: dict, parts: list[dict], config: dict):
    """Expose proven Word-run headings without mutating a source formula.

    A mathematical parenthesis alone is insufficient: require the original
    Word script-run provenance, an expected question subpart and a complete,
    unique set of matching answer headings across at least two subparts.
    """
    candidates = []
    for match in re.finditer(r"⟦(F\d{6})⟧", answer):
        if answer[answer.rfind("\n", 0, match.start()) + 1:match.start()].strip():
            continue
        f = fmap.get(match[1], {})
        latex = f.get("latex") or ""
        prefix = re.match(r"^\s*[（(]([1-9]\d*)[）)]", latex)
        if not prefix:
            continue
        segments = f.get("word_segments") or []
        source_prefix = re.match(r"^\s*[（(]([1-9]\d*)[）)]", segments[0].get("text", "")) if segments else None
        valid = (f.get("status") == "converted" and f.get("origin") == "word_script_sequence"
                 and source_prefix and source_prefix[1] == prefix[1]
                 and latex[prefix.end():].strip())
        candidates.append({"match": match, "prefix": prefix, "formula": f, "valid": bool(valid)})
    if not candidates:
        return answer, [], [], None
    labels = [(m.start(), m[1]) for m in ANSWER_PART.finditer(answer)]
    labels += [(row["match"].start(), row["prefix"][1]) for row in candidates]
    answer_labels = [label for _, label in sorted(labels)]
    part_labels = [part["label"] for part in parts]
    if (not all(row["valid"] for row in candidates) or len(answer_labels) < 2
            or len(set(answer_labels)) != len(answer_labels)
            or set(answer_labels) != set(part_labels)):
        return answer, [], [], "Formula-contained answer headings lack complete source-run and subpart evidence"
    view, maps, derivations, cursor = "", [], [], 0
    for row in candidates:
        match, prefix, f = row["match"], row["prefix"], row["formula"]
        before = answer[cursor:match.start()]
        maps.append({"start": len(view), "end": len(view) + len(before), "source_start": cursor,
                     "source_end": match.start(), "derived": False})
        view += before
        remaining = f["latex"][prefix.end():]
        # Retain the opaque reference while separating answer layout fields;
        # spaces inside the derived LaTeX must never become answer separators.
        replacement = f"({prefix[1]}){match[0]}"
        maps.append({"start": len(view), "end": len(view) + len(replacement), "source_start": match.start(),
                     "source_end": match.end(), "derived": True, "formula_id": match[1]})
        view += replacement
        derivations.append({"source_formula_id": match[1], "original_latex": f["latex"],
                            "removed_prefix": prefix[0], "derived_latex": remaining,
                            "subpart": prefix[1], "source_location": f.get("location", ""),
                            "method": "word_run_subpart_heading", "original_answer_span": [match.start(), match.end()]})
        cursor = match.end()
    maps.append({"start": len(view), "end": len(view) + len(answer) - cursor, "source_start": cursor,
                 "source_end": len(answer), "derived": False})
    view += answer[cursor:]
    return view, maps, derivations, None


def enrich_blank_question(q: dict, blocks: list[dict], config: dict, formulas=None) -> None:
    """Bind all slots or add a local issue; never publish a partially guessed fact.

    ``formulas`` is optional converted formula metadata. It is used solely to
    identify subpart labels hidden inside Word equations, never to alter LaTeX.
    """
    if q.get("kind", "blank") != "blank":
        return
    stem, answer = q.get("stem", ""), q.get("answer", "")
    q.setdefault("original_answer", answer)
    q["blank_binding_version"] = VERSION
    q["knowledge_point"] = ""
    q["blank_slots"], q["answer_bindings"] = [], []
    masked = _masked(stem)
    parts = [{"label": m[1], "start": m.start(), "end": m.end()}
             for m in SUBPART.finditer(stem)]
    fmap = {f["id"]: f for f in formulas or []}
    for match in re.finditer(r"⟦(F\d{6})⟧", stem):
        # Only a line-leading equation can carry the subpart's heading.
        if stem[stem.rfind("\n", 0, match.start()) + 1:match.start()].strip():
            continue
        latex = fmap.get(match[1], {}).get("latex") or ""
        prefix = re.match(r"^\s*[（(]([1-9]\d*)[）)]", latex)
        if prefix:
            parts.append({"label": prefix[1], "start": match.start(), "end": match.end()})
    parts.sort(key=lambda item: item["start"])
    source_spans = _source_spans(q, blocks, stem)
    aliases = {row["label"] for row in q.get("table_slot_aliases", [])
               if row.get("binding") == "same_explicit_label"}
    by_alias = {}
    for match in BLANK.finditer(masked):
        raw = stem[match.start():match.end()]
        numbered = re.fullmatch(r"_+([0-9]+)_+", raw)
        prefix = re.search("([" + CIRCLED + r"])[ \t]*$", masked[:match.start()])
        label = numbered[1] if numbered else prefix[1] if prefix else None
        part = next((part["label"] for part in reversed(parts) if part["end"] <= match.start()), None)
        span = next((span for span in source_spans if span["start"] <= match.start() < span["end"]), None)
        occurrence = {"start": match.start(), "end": match.end(), "text": raw,
                      "location": span["location"] if span else q.get("location", ""),
                      "block_index": span["block_index"] if span else None}
        alias_key = (part, label) if label in aliases else None
        if alias_key and alias_key in by_alias:
            by_alias[alias_key]["occurrences"].append(occurrence)
            continue
        slot = {"id": f"{q.get('id', 'question')}:blank:{len(q['blank_slots']) + 1}",
                "order": len(q["blank_slots"]) + 1, "label": label, "subpart": part,
                "occurrences": [occurrence]}
        q["blank_slots"].append(slot)
        if alias_key:
            by_alias[alias_key] = slot
    slots = q["blank_slots"]
    if not slots:
        _problem(q, "No explicit textual blank slots; a short-answer task cannot be filled automatically", slot_count=0)
        return
    if not answer.strip():
        _problem(q, "No answer to bind to blank slots", slot_count=len(slots))
        return
    answer, view_spans, derivations, view_error = _answer_view(answer, fmap, parts, config)
    if view_error:
        _problem(q, view_error)
        return
    if derivations:
        q["answer_binding_view"] = answer
        q["answer_formula_derivations"] = derivations
    assignments, method = [], ""
    labelled = _labelled_answers(answer, [slot["label"] for slot in slots])
    answer_parts = list(ANSWER_PART.finditer(answer))
    if labelled:
        assignments = [labelled[slot["label"]] for slot in slots]
        method = "explicit_slot_label"
    elif answer_parts:
        # Every answer section must correspond to a visible/evidenced subpart.
        labels = [match[1] for match in answer_parts]
        part_labels = [part["label"] for part in parts]
        slot_groups = {label: [slot for slot in slots if slot["subpart"] == label] for label in part_labels}
        if len(set(labels)) != len(labels) or any(label not in part_labels for label in labels):
            _problem(q, "Answer subpart labels do not uniquely match question subparts", answer_labels=labels, stem_labels=part_labels)
            return
        prefix = answer[:answer_parts[0].start()]
        if prefix.strip():
            # Some source documents omit only the first (1) answer label. Accept
            # only when the stem explicitly supplies exactly that missing group.
            missing = [label for label in part_labels if label not in labels and slot_groups[label]]
            if missing != ["1"] or part_labels[0] != "1":
                _problem(q, "Unlabelled answer prefix cannot be attributed to a subpart")
                return
            first_fields = _fields(prefix, len(slot_groups["1"]))
            if first_fields is None:
                _problem(q, "First subpart answer field count differs from blank count")
                return
            assignments.extend(zip(slot_groups["1"], first_fields))
        for index, match in enumerate(answer_parts):
            end = answer_parts[index + 1].start() if index + 1 < len(answer_parts) else len(answer)
            group = slot_groups[match[1]]
            fields = _fields(answer[match.end():end], len(group), match.end())
            if fields is None:
                _problem(q, "Answer field count differs from blank count within a numbered subpart", subpart=match[1], slot_count=len(group))
                return
            assignments.extend(zip(group, fields))
        by_id = {slot["id"]: field for slot, field in assignments}
        if len(by_id) != len(slots):
            _problem(q, "Some blank subparts have no corresponding answer section")
            return
        assignments = [by_id[slot["id"]] for slot in slots]
        method = "subpart_labels_and_layout"
    else:
        assignments = _fields(answer, len(slots))
        if assignments is None:
            _problem(q, "Unlabelled answer layout does not establish one answer per blank; punctuation is not a separator", slot_count=len(slots))
            return
        method = "single_slot" if len(slots) == 1 else "explicit_layout_fields"
    replacements = []
    for slot, field in zip(slots, assignments):
        for derivation in derivations:
            delimiter = config.get("formula_delimiter", "$$")
            field["text"] = field["text"].replace(
                f"⟦{derivation['source_formula_id']}⟧",
                f"{delimiter}{derivation['derived_latex']}{delimiter}")
        binding = {"slot_id": slot["id"], "answer": field["text"], "method": method,
                   "answer_span": {"start": field["start"], "end": field["end"],
                                   "coordinate": "answer_binding_view" if derivations else "original_answer"},
                   "source_spans": slot["occurrences"]}
        if derivations:
            binding["answer_source_spans"] = []
            for span in view_spans:
                start, end = max(span["start"], field["start"]), min(span["end"], field["end"])
                if start < end:
                    binding["answer_source_spans"].append({
                        "start": span["source_start"] if span["derived"] else span["source_start"] + start - span["start"],
                        "end": span["source_end"] if span["derived"] else span["source_start"] + end - span["start"],
                        "formula_id": span.get("formula_id")})
        q["answer_bindings"].append(binding)
        replacements.extend((item["start"], item["end"], field["text"]) for item in slot["occurrences"])
    completed = stem
    for start, end, value in sorted(replacements, reverse=True):
        completed = completed[:start] + value + completed[end:]
    q["knowledge_point"] = completed
    q["answer"] = ",".join(field["text"] for field in assignments)
