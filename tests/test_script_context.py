"""Word's explicit run alignment is preserved across balanced chemical groups."""
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from qa_parser.document import read_document, _Reader
from test_document import docx, flattened


def run(text, alignment=None):
    props = f'<w:rPr><w:vertAlign w:val="{alignment}"/></w:rPr>' if alignment else ''
    return f'<w:r>{props}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


@pytest.mark.parametrize('pieces,expected', [
    ([('Fe(NO', None), ('3', 'subscript'), (')', None), ('2', 'subscript')], 'Fe(NO_{3})_{2}'),
    ([('CO（NH', None), ('2', 'subscript'), ('）', None), ('2', 'subscript')], 'CO(NH_{2})_{2}'),
    ([('Al', None), ('2', 'subscript'), ('(SO', None), ('4', 'subscript'), (')', None), ('3', 'subscript')], 'Al_{2}(SO_{4})_{3}'),
    ([('(x', None), ('1', 'subscript'), (')', None), ('2', 'superscript')], '(x_{1})^{2}'),
    ([('A(B(C', None), ('1', 'subscript'), (')', None), ('2', 'subscript'), (')', None), ('3', 'subscript')], 'A(B(C_{1})_{2})_{3}'),
])
def test_balanced_group_scripts_keep_direction_and_all_source_locations(tmp_path, pieces, expected):
    source = docx(tmp_path, '<w:p>' + ''.join(run(*p) for p in pieces) + '</w:p>')
    result = read_document(source)
    assert result['issues'] == []
    tokens = flattened(result)
    assert len(tokens) == 1
    assert tokens[0]['origin'] == 'word_group_script_sequence'
    assert tokens[0]['data'].decode() == expected
    assert len(tokens[0]['source_locations']) == len(pieces)
    assert all(f'/w:r[{i}]/w:t' in location for i, location in enumerate(tokens[0]['source_locations'], 1))
    assert '<w:vertAlign' in result['blocks'][0]['source_xml']


@pytest.mark.parametrize('middle', [') ', ')，', '）', '))', ')\t'])
def test_invalid_or_separated_group_is_not_guessed(tmp_path, middle):
    source = docx(tmp_path, '<w:p>' + run('Fe(NO') + run('3', 'subscript') + run(middle) + run('2', 'subscript') + '</w:p>')
    result = read_document(source)
    assert any(i['code'] == 'word_script' for i in result['issues'])
    assert not any(t.get('origin') == 'word_group_script_sequence' for t in flattened(result))


def test_small_question_label_and_surrounding_prose_stay_text(tmp_path):
    source = docx(tmp_path, '<w:p>' + run('答案(3)Al') + run('2', 'subscript') + run('(SO') + run('4', 'subscript') + run(')') + run('3', 'subscript') + run('，后文') + '</w:p>')
    result = read_document(source)
    assert not result['issues']
    assert ''.join(t.get('text', '') for t in flattened(result)) == '答案(3)，后文'
    assert [t['data'].decode() for t in flattened(result) if t['kind'] == 'formula'] == ['Al_{2}(SO_{4})_{3}']


def test_embedded_formula_boundary_is_not_assumed_to_be_script_base(tmp_path):
    source = docx(tmp_path, '<w:p><m:oMath><m:r><m:t>x</m:t></m:r></m:oMath>' + run('2', 'subscript') + '</w:p>')
    result = read_document(source)
    assert [i['code'] for i in result['issues']] == ['word_script']


def test_repeated_nonadjacent_script_keeps_review(tmp_path):
    source = docx(tmp_path, '<w:p>' + run('(N') + run('1', 'subscript') + run(')') + run('2', 'subscript') + run('3', 'superscript') + run('4', 'subscript') + '</w:p>')
    result = read_document(source)
    assert result['issues']
    assert not any(t.get('origin') == 'word_group_script_sequence' for t in flattened(result))


@pytest.mark.parametrize('filename,expected', [
    ('MathType公式化学填空.docx', ['Fe(NO_{3})_{2}', 'Cu(NO_{3})_{2}', 'Al_{2}(SO_{4})_{3}', 'Fe(NO_{3})_{2}', 'Cu(NO_{3})_{2}', 'Zn(NO_{3})_{2}', 'Fe(NO_{3})_{2}', 'Cu(NO_{3})_{2}']),
    ('MathType公式化学填空2.docx', ['CO(NH_{2})_{2}', 'Fe_{2}(SO_{4})_{3}']),
])
def test_actual_ten_source_groups_are_preserved(filename, expected):
    source = Path(__file__).resolve().parents[1] / 'reference' / filename
    result = read_document(source)
    assert result['issues'] == []
    groups = [t for t in flattened(result) if t.get('origin') == 'word_group_script_sequence']
    assert [t['data'].decode() for t in groups] == expected
    # Every reference points to the original run, including the formerly orphaned digit.
    from zipfile import ZipFile
    with ZipFile(source) as archive:
        reader = _Reader(archive)
        for group in groups:
            for location in group['source_locations']:
                node, = reader.root.xpath(location.split(':', 1)[1], namespaces={k: v for k, v in reader.root.nsmap.items() if k})
                assert node.text is not None

@pytest.mark.parametrize('pieces,expected', [
    ([('Ca(HCO', None), ('3', 'subscript'), (')', None), ('2   ', 'subscript')], 'Ca(HCO_{3})_{2   }'),
    ([('Ca（ HCO', None), ('3', 'subscript'), ('）', None), ('2', 'subscript')], 'Ca( HCO_{3})_{2}'),
])
def test_explicit_bracket_group_with_source_spacing(tmp_path, pieces, expected):
    source = docx(tmp_path, '<w:p>' + ''.join(run(*piece) for piece in pieces) + '</w:p>')
    result = read_document(source)
    assert not result['issues']
    tokens = flattened(result)
    assert len(tokens) == 1 and tokens[0]['data'].decode() == expected
