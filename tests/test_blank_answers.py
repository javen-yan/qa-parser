"""Synthetic contracts for typed blank binding, independent of private samples."""
from qa_parser.blank_answers import enrich_blank_question


def question(stem, answer, **extra):
    return {"id": "q1", "number": "1", "kind": "blank", "stem": stem, "answer": answer,
            "raw_text": stem + "【答案】" + answer, "knowledge_point": "", "issues": [],
            "block_indices": [0], "location": "word/document.xml:/p[1]", **extra}


def enrich(q, **kwargs):
    original = q["raw_text"]
    enrich_blank_question(q, [{"index": 0, "text": "1．" + q["stem"], "location": q["location"]}], {}, **kwargs)
    assert q["raw_text"] == original
    return q


def test_one_slot_preserves_synonym_and_internal_punctuation():
    q = enrich(question("反射类型是____。", "非条件反射/简单反射"))
    assert q["answer"] == "非条件反射/简单反射"
    assert q["knowledge_point"] == "反射类型是非条件反射/简单反射。"
    q = enrich(question("气候、植被特点表现为：____。", "气候温暖湿润，植被茂密"))
    assert len(q["answer_bindings"]) == 1
    assert q["answer"] == "气候温暖湿润，植被茂密"
    assert q["stem"].endswith("____。")


def test_multi_slots_use_ascii_comma_and_do_not_split_punctuation():
    q = enrich(question("____，____。", "甲、乙     丙/丁"))
    assert not q["issues"]
    assert q["answer"] == "甲、乙,丙/丁"
    assert q["original_answer"] == "甲、乙     丙/丁"
    assert q["knowledge_point"] == "甲、乙，丙/丁。"
    assert q["answer_bindings"][0]["source_spans"][0]["block_index"] == 0


def test_numbered_subparts_bind_locally_and_preserve_shared_text():
    q = enrich(question("公共材料。\n(1)____与____。\n(2)____。", "(1)甲     乙\n(2)丙"))
    assert not q["issues"]
    assert q["answer"] == "甲,乙,丙"
    assert q["knowledge_point"] == "公共材料。\n(1)甲与乙。\n(2)丙。"
    assert [slot["subpart"] for slot in q["blank_slots"]] == ["1", "1", "2"]


def test_image_before_subpart_heading_does_not_hide_number():
    q = enrich(question("公共材料\n⟦I000001⟧（1）____\n（2）____", "（1）甲\n（2）乙"))
    assert not q["issues"]
    assert q["answer"] == "甲,乙"
    assert q["knowledge_point"] == "公共材料\n⟦I000001⟧（1）甲\n（2）乙"


def test_numbered_slots_are_one_question_not_five_questions():
    q = enrich(question("界面__76__千米，速度__77__。", "76．39～41（或17）    77．增加"))
    assert not q["issues"]
    assert [slot["label"] for slot in q["blank_slots"]] == ["76", "77"]
    assert q["answer"] == "39～41（或17）,增加"
    assert q["knowledge_point"] == "界面39～41（或17）千米，速度增加。"


def test_answer_sections_out_of_order_still_bind_by_explicit_label():
    q = enrich(question("(1)____。\n(2)____。", "(2)乙\n(1)甲"))
    assert not q["issues"]
    assert q["answer"] == "甲,乙"


def test_numbered_slot_answers_bind_by_label_not_layout_order():
    q = enrich(question("__1__和__2__", "2．乙    1．甲"))
    assert not q["issues"]
    assert q["knowledge_point"] == "甲和乙"
    assert q["answer"] == "甲,乙"


def test_numbered_slot_decimal_value_is_not_an_answer_heading():
    q = enrich(question("距离__1__米", "1.5"))
    assert not q["issues"]
    assert q["answer"] == "1.5" and q["knowledge_point"] == "距离1.5米"


def test_conflicting_numbered_labels_never_fall_back_to_positional_binding():
    for stem, answer in (("__1__和__2__", "1．甲    3．乙"),
                         ("__1__和__2__", "1．甲    1．乙"),
                         ("__1__和__1__", "甲    乙"),
                         ("(1)____\n(1)____", "(1)甲    乙")):
        q = enrich(question(stem, answer))
        assert q["issues"] and q["answer"] == answer
        assert q["answer_bindings"] == [] and not q["knowledge_point"]


def test_circled_slot_labels_cannot_silently_fall_back_to_layout():
    q = enrich(question("①____②____", "②乙    ①甲"))
    assert not q["issues"] and q["answer"] == "甲,乙"
    for stem, answer in (("①____②____", "①甲    ③乙"), ("①____①____", "甲    乙")):
        q = enrich(question(stem, answer))
        assert q["issues"] and not q["knowledge_point"]


def test_repeated_section_numbers_keep_unambiguous_layout_bindings():
    q = enrich(question("(1)已有说明。\n(1)____\n(2)____", "(1)甲\n(2)乙"))
    assert not q["issues"] and q["answer"] == "甲,乙"
    q = enrich(question("材料一\n(1)____\n材料二\n(1)____", "甲    乙"))
    assert not q["issues"] and q["answer"] == "甲,乙"


def test_missing_or_extra_answer_sections_require_review():
    for answer in ("(1)甲", "(1)甲\n(1)乙", "(1)甲\n(3)乙"):
        q = enrich(question("(1)____。\n(2)____。", answer))
        assert q["issues"][0]["code"] == "blank_answer_alignment"
        assert q["knowledge_point"] == ""
        assert q["answer"] == answer


def test_equal_global_counts_cannot_hide_subpart_misalignment():
    q = enrich(question("(1)____与____。\n(2)____。", "(1)甲\n(2)乙     丙"))
    assert q["issues"]
    assert q["answer_bindings"] == []
    assert not q["knowledge_point"]


def test_no_punctuation_guessing_for_multiple_slots():
    for answer in ("甲，乙", "甲,乙", "甲/乙", "甲、乙", "甲 乙"):
        q = enrich(question("____与____", answer))
        assert q["issues"]
        assert q["answer"] == answer
        assert not q["knowledge_point"]


def test_explicit_table_aliases_share_one_answer_but_unproven_repetitions_do_not():
    stem = "表格①____\n答题①____"
    q = enrich(question(stem, "甲", table_slot_aliases=[{"label": "①", "binding": "same_explicit_label"}]))
    assert not q["issues"]
    assert len(q["blank_slots"]) == 1
    assert len(q["blank_slots"][0]["occurrences"]) == 2
    assert q["knowledge_point"] == "表格①甲\n答题①甲"
    unproven = enrich(question(stem, "甲"))
    assert unproven["issues"]
    assert len(unproven["blank_slots"]) == 2


def test_circled_answers_as_values_are_not_misread_as_numbered_sections():
    q = enrich(question("①____②____③____", "④     ①     ⑤"))
    assert not q["issues"]
    assert q["answer"] == "④,①,⑤"


def test_no_slots_is_explicitly_unresolved():
    q = enrich(question("解释太阳能的用途。", "发电"))
    assert q["issues"]
    assert not q["knowledge_point"]


def test_empty_parentheses_are_explicit_text_slots_and_keep_delimiters():
    q = enrich(question("(1)反应是否正确（    ）\n(2)理由____", "(1)不正确\n(2)缺少反应条件"))
    assert not q["issues"]
    assert q["knowledge_point"] == "(1)反应是否正确（不正确）\n(2)理由缺少反应条件"
    assert q["stem"] == "(1)反应是否正确（    ）\n(2)理由____"


def test_formula_references_are_preserved_and_never_count_as_blank():
    q = enrich(question("⟦F000001⟧对应____，图⟦I000001⟧。", "⟦F000002⟧"))
    assert q["knowledge_point"] == "⟦F000001⟧对应⟦F000002⟧，图⟦I000001⟧。"
    assert len(q["blank_slots"]) == 1
    q = enrich(question(r"$$R_{__x}$$对应____", "甲"))
    assert len(q["blank_slots"]) == 1
    assert q["knowledge_point"] == r"$$R_{__x}$$对应甲"
    q = enrich(question("式（⟦F000001⟧）对应____", "甲"))
    assert len(q["blank_slots"]) == 1


def test_formula_embedded_stem_heading_can_supply_evidenced_group():
    q = enrich(question("(1)____\n⟦F000001⟧时____", "(1)甲\n(2)乙"),
               formulas=[{"id": "F000001", "latex": "(2)t_2"}])
    assert not q["issues"]
    assert q["knowledge_point"] == "(1)甲\n⟦F000001⟧时乙"


def test_formula_embedded_answer_label_needs_review_not_math_rewriting():
    q = enrich(question("(1)____", "⟦F000001⟧"), formulas=[{"id": "F000001", "latex": "(1)H_2O"}])
    assert q["issues"]
    assert not q["knowledge_point"]
    assert q["answer"] == "⟦F000001⟧"


def test_source_run_heading_extraction_has_original_and_derived_evidence():
    formula = {"id": "F000001", "latex": "(3)H_2O", "status": "converted",
               "origin": "word_script_sequence", "word_segments": [{"text": "(3)H"}, {"script": "_", "text": "2"}, {"text": "O"}]}
    q = question("(1)____\n(2)____\n(3)____", "(1)甲\n(2)乙\n⟦F000001⟧")
    enrich_blank_question(q, [], {"formula_delimiter": "@@"}, [formula])
    assert not q["issues"]
    assert q["answer"] == "甲,乙,@@H_2O@@"
    assert q["knowledge_point"] == "(1)甲\n(2)乙\n(3)@@H_2O@@"
    assert formula["latex"] == "(3)H_2O"
    assert q["original_answer"] == "(1)甲\n(2)乙\n⟦F000001⟧"
    assert q["answer_formula_derivations"][0]["source_formula_id"] == "F000001"
    assert q["answer_formula_derivations"][0]["removed_prefix"] == "(3)"
    assert q["answer_bindings"][2]["answer_source_spans"][0]["formula_id"] == "F000001"


def test_arithmetic_parenthetical_is_not_stripped_without_question_heading_evidence():
    formula = {"id": "F000001", "latex": "(3)x_2", "status": "converted",
               "origin": "word_script_sequence", "word_segments": [{"text": "(3)x"}, {"script": "_", "text": "2"}]}
    q = enrich(question("结果为____", "⟦F000001⟧"), formulas=[formula])
    assert q["issues"]
    assert q["answer"] == "⟦F000001⟧"
    assert not q.get("answer_formula_derivations")


def test_spaces_in_derived_formula_do_not_split_answer_fields():
    formula = {"id": "F000001", "latex": r"(2)H_2O\quad  CO_2", "status": "converted",
               "origin": "word_script_sequence", "word_segments": [{"text": "(2)H"}, {"script": "_", "text": "2"}]}
    q = enrich(question("(1)____\n(2)____和____", "(1)甲\n⟦F000001⟧     乙"), formulas=[formula])
    assert not q["issues"]
    assert q["answer"] == r"甲,$$H_2O\quad  CO_2$$,乙"


def test_unlabelled_first_answer_requires_explicit_missing_first_subpart():
    q = enrich(question("(1)____\n(2)____", "甲\n(2)乙"))
    assert not q["issues"]
    assert q["answer"] == "甲,乙"
    q = enrich(question("(2)____\n(3)____", "甲\n(3)乙"))
    assert q["issues"]
