from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZipFile

import pytest

from qa_parser.classification import classify_document


def make_docx(path, paragraphs):
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, 'w') as archive:
        body = ''.join('<w:p><w:r><w:t xml:space="preserve">' + escape(p) + '</w:t></w:r></w:p>' for p in paragraphs)
        archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body + '</w:body></w:document>')
    return path


def choice(number='1'):
    return [f'{number}．哪个正确？', 'A．甲 B．乙 C．丙 D．丁', '【答案】B']


def blank(number='1'):
    return [f'{number}．填入答案____。', '【答案】铁']


def test_filename_and_heading_are_not_decisive(tmp_path):
    source = make_docx(tmp_path / '填空题.docx', ['一、填空题', *choice()])
    result = classify_document(source)
    assert result['status'] == 'classified' and result['kind'] == 'choice'
    assert result['questions'][0]['evidence']
    assert result['questions'][0]['location'].startswith('word/document.xml:')


def test_plain_blank_detected_even_choice_filename(tmp_path):
    result = classify_document(make_docx(tmp_path / '选择题.docx', blank()))
    assert result['kind'] == 'blank'


def test_subquestion_options_are_part_of_one_blank_large_question(tmp_path):
    source = make_docx(tmp_path / 'example.docx', ['1．公共题干', '(1)填入金属____。', '(2)选择正确项____。', 'A．甲 B．乙 C．丙 D．丁', '【答案】(1)铁', '(2)B'])
    result = classify_document(source)
    assert result['kind'] == 'blank' and result['total'] == 1


def test_multiple_blanks_with_options_and_unnumbered_answers(tmp_path):
    source = make_docx(tmp_path / 'example.docx', ['1．金属包括____。选出物品____。', 'A．甲 B．乙 C．丙 D．丁', '【答案】合金     A'])
    assert classify_document(source)['kind'] == 'blank'


def test_choice_with_numbered_assertions_and_letter_answer(tmp_path):
    source = make_docx(tmp_path / 'example.docx', ['1．下列哪些论述正确？', '(1)甲', '(2)乙', 'A．只有甲 B．只有乙 C．都对 D．都错', '【答案】C'])
    assert classify_document(source)['kind'] == 'choice'


def test_top_level_mixed_document_rejected(tmp_path):
    result = classify_document(make_docx(tmp_path / 'mixed.docx', choice() + blank('2')))
    assert result['status'] == 'mixed' and result['kind'] is None
    assert result['counts'] == {'choice': 1, 'blank': 1, 'unknown': 0}


@pytest.mark.parametrize('paragraphs', [
    ['一、选择题', '这不是题目'],
    ['1．说明这个过程。', '【答案】详细解释'],
    ['1．请选择____。', 'A．甲', 'B．乙', '【答案】A'],
    ['一、判断题', '1．水是液体____。', '【答案】对'],
])
def test_uncertain_or_unsupported_never_guessed(tmp_path, paragraphs):
    result = classify_document(make_docx(tmp_path / 'unknown.docx', paragraphs))
    assert result['kind'] is None and result['status'] == 'unknown'


def test_missing_choice_answer_left_to_core_validation(tmp_path):
    result = classify_document(make_docx(tmp_path / 'missing.docx', choice()[:-1]))
    assert result['kind'] == 'choice'


def test_corrupt_file_diagnostic_separated_from_reason(tmp_path):
    source = tmp_path / 'broken.docx'
    source.write_bytes(b'not a Word file')
    result = classify_document(source)
    assert result['status'] == 'failed' and result['kind'] is None
    assert 'ValueError' in result['diagnostic']
    assert 'ValueError' not in result['reason']


def test_nine_reference_documents_body_classification():
    root = Path(__file__).resolve().parents[1] / 'reference'
    expected = {
        'MathType公式化学.docx': 'choice', 'MathType公式化学2.docx': 'choice',
        'MathType公式化学3.docx': 'choice', 'MathType公式物理.docx': 'choice',
        'MathType公式物理2.docx': 'choice', '微软公式化学.docx': 'choice',
        '微软公式物理.docx': 'choice', 'MathType公式化学填空.docx': 'blank',
        'MathType公式化学填空2.docx': 'blank',
    }
    for filename, kind in expected.items():
        result = classify_document(root / filename)
        assert (result['status'], result['kind'], result['total']) == ('classified', kind, 100)
        assert result['counts'][kind] == 100


@pytest.mark.parametrize('answer', ['选A', '答案为A', '答案为     A', 'A项', '应选 A'])
def test_choice_blanks_with_prose_answer_never_misclassified_blank(tmp_path, answer):
    source = make_docx(tmp_path / 'example.docx', ['1．请选出____和____的正确组合。', 'A．甲 B．乙 C．丙 D．丁', f'【答案】{answer}'])
    assert classify_document(source)['kind'] == 'choice'


def test_numbered_essay_questions_are_not_blank_evidence(tmp_path):
    source = make_docx(tmp_path / 'example.docx', ['1．回答下列问题。', '(1)解释原因。', '(2)论述影响。', '【答案】(1)解释正文', '(2)论述正文'])
    assert classify_document(source)['status'] == 'unknown'
