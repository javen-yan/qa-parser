"""Synthetic structure regressions; no private question-bank text or assets."""
from qa_parser.question_structure import build_questions
from qa_parser.core import _questions


def blocks(*texts):
    return [{'index': i, 'kind': 'paragraph', 'location': f'word/document.xml:/body/p[{i+1}]',
             'text': text, 'tokens': [{'kind': 'text', 'text': text}]}
            for i, text in enumerate(texts)]


OPTIONS = 'A．红 B．蓝 C．绿 D．黄'


def test_shared_material_children_own_numbered_answers_and_analysis():
    bs = blocks('一、单选题', '阅读示意图，完成下面小题。', '⟦I000001⟧',
                '4．颜色一？', OPTIONS, '5．颜色二？', OPTIONS,
                '【答案】4．A    5．D', '【解析】4．第一段解释。', '5．第二段解释。',
                '【点睛】共同说明。', '6．独立题。', OPTIONS, '【答案】B')
    qs, preamble, structure = build_questions(bs, 'auto', {})
    assert [q['number'] for q in qs] == ['4', '5', '6']
    assert [q['answer'] for q in qs] == ['A', 'D', 'B']
    assert all(q['kind'] == 'choice' and not q['issues'] for q in qs)
    a, b, c = qs
    assert a['parent_id'] == b['parent_id'] and c['parent_id'] is None
    assert a['material_refs'] == b['material_refs'] == [1, 2]
    assert '⟦I000001⟧' in a['stem'] and '⟦I000001⟧' in b['raw_text']
    assert '⟦I000001⟧' not in c['raw_text']
    assert '第一段解释' in a['analysis'] and '第二段解释' not in a['analysis']
    assert '第二段解释' in b['analysis'] and '第一段解释' not in b['analysis']
    assert all('共同说明' in q['analysis'] for q in (a, b))
    assert structure['original_question_count'] == 2
    assert structure['subquestion_count'] == 2
    assert structure['output_candidate_count'] == 3
    assert [b['text'] for b in preamble] == ['一、单选题']
    assert a['source_spans'] and a['answer_source_spans']


def test_shared_intro_does_not_leak_into_previous_explanation():
    bs = blocks('1．独立题？', OPTIONS, '【答案】A', '【详解】只属于第一题。',
                '另一幅图，回答下列小题。', '⟦I000002⟧',
                '2．题二？', OPTIONS, '3．题三？', OPTIONS,
                '【答案】2．B    3．C', '【解析】2．解释二。', '3．解释三。')
    qs, _, structure = build_questions(bs, 'auto', {})
    assert len(qs) == 3 and len(structure['groups']) == 1
    assert '另一幅图' not in qs[0]['analysis']
    assert all('另一幅图' in q['stem'] for q in qs[1:])


def test_explicit_material_group_missing_answer_number_is_review_not_guess():
    bs = blocks('读图完成下面小题。', '1．问题一？', OPTIONS, '2．问题二？', OPTIONS,
                '【答案】1．A    3．B', '【解析】1．解释一。', '2．解释二。')
    qs, _, _ = build_questions(bs, 'auto', {})
    assert len(qs) == 2
    assert all(q['answer'] == '' for q in qs)
    assert all(any(i['code'] == 'group_answer_binding' for i in q['issues']) for q in qs)


def test_single_child_numbered_answer_group_and_trailing_theme():
    bs = blocks('阅读材料，完成下面小题。【主题】', '⟦I000001⟧',
                '8．一个问题？', OPTIONS, '【答案】8．C\n【解析】8．解释。')
    qs, _, st = build_questions(bs, 'auto', {})
    assert len(qs) == len(st['groups']) == 1
    assert qs[0]['answer'] == 'C' and '解释。' in qs[0]['analysis']
    assert qs[0]['material_refs'] == [0, 1]


def test_numbered_blanks_are_one_question_and_previous_answer_stays_owned():
    bs = blocks('75．先前题目。', '(1)先前空____。', '(2)另一个空____。',
                '【答案】(1)甲', '(2)乙', '新概念：', '①定义。',
                '（1）第一部分__76__和__77__。', '（2）第二部分__78__。',
                '【答案】76．红    77．蓝    78．绿', '79．后一道____。', '【答案】黄')
    qs, _, st = build_questions(bs, 'blank', {})
    assert [q['number'] for q in qs] == ['75', '76–78', '79']
    assert qs[0]['answer'] == '(1)甲\n(2)乙'
    assert qs[1]['stem'].startswith('新概念：')
    assert qs[1]['answer'] == '76．红    77．蓝    78．绿'
    assert qs[1]['numbered_blank_ids'] == ['76', '77', '78']
    assert qs[1]['organization'] == 'numbered_blanks'
    assert st['original_question_count'] == 3 and st['subquestion_count'] == 0


def test_analysis_steps_do_not_create_questions_or_change_legacy_text():
    bs = blocks('1．题目？', OPTIONS, '【答案】A', '【详解】解释。',
                '1.解释步骤一。', '2.解释步骤二。', '2．第二题？', OPTIONS, '【答案】B')
    legacy, _ = _questions(bs, 'choice', {})
    qs, _, st = build_questions(bs, 'choice', {})
    assert len(qs) == 2 and not st['groups']
    for before, after in zip(legacy, qs):
        assert all(before[key] == after[key] for key in ('raw_text', 'stem', 'answer', 'analysis', 'options', 'id'))


def test_three_options_preserved_with_explicit_labels():
    qs, _, _ = build_questions(blocks('1．选择？', 'A．红 B．蓝 C．绿', '【答案】C'), 'auto', {})
    assert qs[0]['kind'] == 'choice'
    assert qs[0]['option_labels'] == ['A', 'B', 'C']
    assert qs[0]['options'] == {'A': '红', 'B': '蓝', 'C': '绿'}
    assert not qs[0]['issues']


def test_mixed_and_unsupported_sibling_is_local():
    qs, _, _ = build_questions(blocks('1．选择？', OPTIONS, '【答案】B',
                '2．填____。', '【答案】红', '3．解释为什么。', '【答案】完整陈述。'), 'auto', {})
    assert [q['kind'] for q in qs] == ['choice', 'blank', 'unsupported']
    assert not qs[0]['issues'] and not qs[1]['issues']
    assert qs[2]['issues'][0]['code'] == 'unsupported_kind'


def test_material_source_order_and_group_ids_repeat_deterministically():
    bs = blocks('读图完成下面小题。', '⟦I000001⟧⟦F000001⟧', '1．选择？', OPTIONS,
                '2．选择？', OPTIONS, '【答案】1．A    2．B', '【解析】1．甲。', '2．乙。')
    first = build_questions(bs, 'auto', {})
    assert build_questions(bs, 'auto', {}) == first
    for q in first[0]:
        assert q['source_spans'] == sorted(q['source_spans'], key=lambda s: s['block_index'])
        assert q['number_path'] == [q['parent_id'], q['number']]


def test_numbered_blank_keys_accept_numeric_answers_after_fullwidth_separator():
    bs = blocks('1．前题____。', '【答案】甲', '后续陈述：',
                '高度__2__，距离__3__。', '【答案】2．12～15（或9）    3．2600',
                '4．后题____。', '【答案】乙')
    qs, _, _ = build_questions(bs, 'blank', {})
    assert [q['number'] for q in qs] == ['1', '2–3', '4']
    assert qs[1]['answer'] == '2．12～15（或9）    3．2600'
    assert all(q['kind'] == 'blank' for q in qs)


def test_explicit_number_range_material_instruction():
    bs = blocks('读图完成1～2题。', '⟦I000001⟧', '1．选择？', OPTIONS,
                '2．选择？', OPTIONS, '【答案】1．A  2．B', '【解析】1．甲。', '2．乙。')
    qs, _, st = build_questions(bs, 'auto', {})
    assert len(st['groups']) == 1
    assert all(q['material_refs'] == [0, 1] and '⟦I000001⟧' in q['stem'] for q in qs)


def test_explicit_multiline_material_is_not_previous_question_analysis():
    bs = blocks('1．独立题？', OPTIONS, '【答案】D', '【详解】独立解释。',
                '材料：这是材料第一段。', '这是材料第二段。', '⟦I000001⟧', '据此完成2—3题。',
                '2．选择？', OPTIONS, '3．填____。', '【答案】2．A  3．红色',
                '【解析】2．解释二。', '3．解释三。')
    qs, _, st = build_questions(bs, 'auto', {})
    assert len(qs) == 3 and len(st['groups']) == 1
    assert '材料第一段' not in qs[0]['analysis']
    assert [q['kind'] for q in qs] == ['choice', 'choice', 'blank']
    for q in qs[1:]:
        assert q['material_refs'] == [4, 5, 6, 7]
        assert '材料第一段' in q['stem'] and '材料第二段' in q['stem']
        assert '⟦I000001⟧' in q['raw_text']
    assert qs[2]['answer'] == '红色'


def test_missing_child_answer_does_not_invalidate_explicit_sibling_binding():
    bs = blocks('完成下面小题。', '1．选择？', OPTIONS, '2．选择？', OPTIONS,
                '【答案】1．B', '【解析】1．解释一。', '2．解释二。')
    qs, _, _ = build_questions(bs, 'auto', {})
    assert qs[0]['answer'] == 'B' and not qs[0]['issues']
    assert qs[1]['answer'] == ''
    assert any(i['code'] == 'group_answer_binding' for i in qs[1]['issues'])


def test_duplicate_question_after_group_is_retained_and_reported():
    bs = blocks('完成下面小题。', '1．选择？', OPTIONS, '2．选择？', OPTIONS,
                '【答案】1．A  2．B', '【解析】1．解释一。', '2．解释二。',
                '2．另一个独立题？', OPTIONS, '【答案】C', '【详解】独立解释。')
    qs, _, _ = build_questions(bs, 'auto', {})
    assert [q['number'] for q in qs] == ['1', '2', '2']
    assert [q['answer'] for q in qs] == ['A', 'B', 'C']
    assert not qs[0]['issues']
    assert all(any(i['code'] == 'duplicate_number' for i in q['issues']) for q in qs[1:])
    assert '另一个独立题' not in qs[1]['analysis']
