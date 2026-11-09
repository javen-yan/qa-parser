"""Synthetic table-role regressions; no private sample content is included."""
from qa_parser.table_policy import apply_table_policy


def paragraph(index, text):
    loc = f"doc/p[{index}]"
    return {"index": index, "kind": "paragraph", "text": text, "location": loc,
            "tokens": [{"kind": "text", "text": text, "location": loc}]}


def row(index, values, table="doc/table[1]", row_number=0):
    tokens, cells = [], []
    for n, value in enumerate(values):
        if n:
            tokens.append({"kind": "text", "text": "\t"})
        start = len(tokens)
        loc = f"{table}/row[{row_number}]/cell[{n}]"
        if isinstance(value, str):
            tokens.append({"kind": "text", "text": value, "location": loc})
        else:
            tokens.extend({"location": loc, **t} for t in value)
        cells.append({"index": n, "location": loc, "token_start": start, "token_end": len(tokens)})
    return {"index": index, "kind": "table_row", "location": f"{table}/row[{row_number}]",
            "row": row_number, "table_location": table, "tokens": tokens, "cells": cells}


def run(blocks, kind="blank", **fields):
    q = {"kind": kind, "block_indices": [b["index"] for b in blocks], "issues": [], **fields}
    apply_table_policy(q, blocks, {})
    return q


def test_static_table_not_skipped_because_body_has_blanks():
    q = run([paragraph(0, "解释____和____。"), row(1, ["过程", "给定关系"]),
             row(2, ["变化", "X₂+Y₂=2XY"], row_number=1)])
    assert q["table_roles"][0]["role"] == "static_material"
    assert not q["issues"]


def test_option_layout_preserved_even_when_options_contain_literal_blank():
    q = run([row(1, ["A．____", "B．甲", "C．乙", "D．丙"])], kind="choice")
    assert q["table_roles"][0]["role"] == "layout"
    assert not q["issues"]


def test_underlined_whitespace_is_interaction_and_keeps_source_cells():
    blocks = [row(0, ["类别", "说明"]), row(1, ["甲", [{"kind": "text", "text": "①"},
               {"kind": "text", "text": " ", "underline": True}]], row_number=1)]
    q = run(blocks)
    role = q["table_roles"][0]
    assert role["role"] == "answer_interaction"
    assert role["slots"][0]["label"] == "①"
    assert q["issues"][0]["policy_skip"] is True
    assert q["issues"][0]["affected_block_indices"] == [1]
    assert "cell[1]" in role["slots"][0]["location"]
    apply_table_policy(q, blocks, {})
    assert len(q["issues"]) == 1


def test_repeated_body_answer_area_has_explicit_alias_without_erasing_table():
    blocks = [row(0, ["类别", "说明"]), row(1, ["①____", "现有材料"], row_number=1),
              row(2, ["②____", "另项材料"], row_number=2), paragraph(3, "①________②________")]
    q = run(blocks)
    assert [a["label"] for a in q["table_slot_aliases"]] == ["①", "②"]
    assert q["table_slot_aliases"][0]["body_occurrences"][0]["block_index"] == 3
    assert q["issues"][0]["policy_skip"]
    assert blocks[1]["tokens"][0]["text"] == "①____"


def test_same_label_in_prose_or_multiple_tables_does_not_guess_alias():
    blocks = [row(0, ["①____", "材料"]), paragraph(1, "另一个问题①____")]
    assert run(blocks)["table_slot_aliases"] == []
    blocks += [row(2, ["①____", "另一材料"], table="doc/table[2]"), paragraph(3, "①____")]
    assert run(blocks)["table_slot_aliases"] == []


def test_literal_math_and_formula_tokens_do_not_become_blank_slots():
    q = run([row(0, [[{"kind": "formula", "id": "F000001"}], r"$$x_{__a__}$$", "①第一步", "__name__"])])
    assert q["table_roles"][0]["role"] == "static_material"
    assert not q["issues"]


def test_mixed_table_localizes_only_rows_with_interaction():
    q = run([row(0, ["类别", "内容"]), row(1, ["甲", "____"], row_number=1),
             row(2, ["乙", "已知"], row_number=2)])
    assert q["table_roles"][0]["role"] == "mixed"
    assert q["issues"][0]["affected_block_indices"] == [1]


def test_unknown_metadata_is_review_not_policy_skip():
    q = run([{"index": 0, "kind": "table_row", "location": "doc/table/row", "text": "①____", "tokens": []}])
    assert q["table_roles"][0]["role"] == "unknown"
    assert q["issues"][0]["code"] == "table_role_unknown"
    assert not q["issues"][0].get("policy_skip")


def test_answer_section_tables_not_mistaken_for_prompt_interaction():
    q = run([paragraph(0, "问题____"), paragraph(1, "【答案】甲"), row(2, ["①____", "说明"] )])
    assert q["table_roles"] == []
    assert not q["issues"]


def test_only_owned_and_inherited_material_tables_are_examined():
    blocks = [row(0, ["材料", "已知"]), paragraph(1, "1．问____"), row(2, ["____", "不属本题"])]
    q = run(blocks, block_indices=[1], material_refs=[0])
    assert len(q["table_roles"]) == 1
    assert q["table_roles"][0]["role"] == "static_material"
    assert not q["issues"]


def test_empty_cells_require_explicit_table_filling_instruction():
    table = row(1, ["名称", ""])
    assert not run([table])["issues"]
    q = run([paragraph(0, "请填写下表。"), table])
    assert q["table_roles"][0]["role"] == "answer_interaction"
    assert q["table_roles"][0]["slots"][0]["empty_cell"]
    assert q["issues"][0]["policy_skip"]


def test_merged_empty_cell_is_not_an_answer_slot():
    table = row(1, ["名称", ""])
    table["cells"][1]["properties_xml"] = '<w:tcPr><w:vMerge/></w:tcPr>'
    q = run([paragraph(0, "请填写下表。"), table])
    assert not q["issues"]


def test_letter_label_adjacent_to_long_underscore_run_is_interaction():
    q = run([row(0, ["类别", "特征"]), row(1, ["示例", "由a________组成，呈b________状"], row_number=1)])
    role = q["table_roles"][0]
    assert role["role"] == "answer_interaction"
    assert len(role["slots"]) == 2
    assert q["issues"][0]["policy_skip"] is True
    # Raw formula tokens and their subscript syntax stay opaque.
    q = run([row(0, [[{"kind": "formula", "id": "F000001"}], r"$$R_{a________}$$", "__name__"])])
    assert q["table_roles"][0]["role"] == "static_material"
    assert not q["issues"]
