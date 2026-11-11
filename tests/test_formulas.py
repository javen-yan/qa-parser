"""Semantic and fail-closed checks, using real fixtures when available."""
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import pytest

from qa_parser.formulas import convert_mathtype, convert_omml, _audit_mtef
from qa_parser.vendor import mtef

NS = 'http://schemas.openxmlformats.org/officeDocument/2006/math'
REFERENCE = Path(__file__).resolve().parents[1] / 'reference'


def formula(body):
    return f'<m:oMath xmlns:m="{NS}">{body}</m:oMath>'.encode()


def test_omml_fraction_preserves_numerator_denominator():
    out = convert_omml(formula('<m:f><m:num><m:r><m:t>U</m:t></m:r></m:num>'
                               '<m:den><m:r><m:t>I</m:t></m:r></m:den></m:f>'))
    assert out['status'] == 'converted'
    assert out['latex'] == r'\frac{U}{I}'


def test_omml_subscripts_and_reaction_condition():
    result = convert_omml(formula('<m:sSub><m:e><m:r><m:t>CO</m:t></m:r></m:e>'
                                  '<m:sub><m:r><m:t>2</m:t></m:r></m:sub></m:sSub>'))
    assert result['status'] == 'converted'
    assert result['latex'] == '{CO}_{2}'
    result = convert_omml(formula('<m:limUpp><m:e><m:r><m:t>→</m:t></m:r></m:e>'
                                  '<m:lim><m:r><m:t>点燃</m:t></m:r></m:lim></m:limUpp>'))
    assert result['status'] == 'converted'
    assert r'\rightarrow' in result['latex'] and r'\limits^{点燃}' in result['latex']


@pytest.mark.parametrize('body', [
    '<m:unknown><m:r><m:t>x</m:t></m:r></m:unknown>',
    '<m:f><m:num><m:r><m:t>1</m:t></m:r></m:num></m:f>',
    '<m:eqArr/>',
    '<m:r><m:t>10%</m:t></m:r>',
    '<m:r><m:t>\ue000</m:t></m:r>',
])
def test_omml_does_not_silently_drop_unknown_or_incomplete_structure(body):
    result = convert_omml(formula(body))
    assert result['status'] == 'review'
    assert result['reason']
    assert result['latex'] is None


def test_invalid_inputs_are_review_not_process_errors():
    assert convert_omml(b'<broken>')['status'] == 'review'
    assert convert_mathtype(b'not an OLE file')['status'] == 'review'
    assert convert_omml(b'<!DOCTYPE test><x/>')['status'] == 'review'


def test_mtef_unknown_template_private_symbol_and_embellishment_are_not_flattened():
    for node in [mtef.Tmpl(14, 36, 0, []), mtef.Text('\ue000', mtcode=0xe000),
                 mtef.Text('x', mtcode=ord('x'), embell=[250])]:
        reasons = []
        _audit_mtef(node, reasons)
        assert reasons


def test_real_physics_pair_preserves_first_subscript_and_fraction():
    mt = REFERENCE / 'MathType公式化学3.docx'
    om = REFERENCE / '微软公式物理.docx'
    if not mt.exists() or not om.exists():
        pytest.skip('Reference fixtures not present')
    with ZipFile(om) as archive:
        root = ET.fromstring(archive.read('word/document.xml'))
        formulas = list(root.iter(f'{{{NS}}}oMath'))
        outputs = [convert_omml(ET.tostring(x)) for x in formulas[:3]]
    assert [x['status'] for x in outputs] == ['converted'] * 3
    assert outputs[0]['latex'] == '{R}_{0}'
    assert outputs[2]['latex'] == r'{R}_{x}=\frac{{U}_{x}}{{I}_{x}}'
    with ZipFile(mt) as archive:
        names = sorted((n for n in archive.namelist() if n.startswith('word/embeddings/')
                        and not n.endswith('/')), key=lambda n: int(''.join(c for c in n if c.isdigit())))
        outputs = [convert_mathtype(archive.read(n)) for n in names[:3]]
    assert [x['status'] for x in outputs] == ['converted'] * 3
    assert outputs[0]['latex'] == 'R_0'
    assert outputs[2]['latex'] == r'R_x=\frac{U_x}{I_x}'


def test_real_chemical_arrow_is_not_silently_reduced_to_condition_text():
    source = REFERENCE / 'MathType公式化学2.docx'
    if not source.exists():
        pytest.skip('Reference fixtures not present')
    with ZipFile(source) as archive:
        result = convert_mathtype(archive.read('word/embeddings/oleObject1.bin'))
    assert result['status'] == 'converted'
    assert result['latex'] == r'\xrightarrow{\text{点燃}}'


def test_unicode_text_units_and_blank_square_are_portable_latex():
    from qa_parser.formulas import _adapt_mtef_tree, _merge_text_runs
    chars = [mtef.Text(c, typeface=12, mtcode=ord(c)) for c in '催化剂、℃□']
    reasons = []
    source = mtef.Line(chars)
    _audit_mtef(source, reasons)
    assert not reasons
    assert _merge_text_runs(mtef.to_latex(_adapt_mtef_tree(source))) == (
        r'\text{催化剂、}{}^{\circ}\mathrm{C}\square{}')
    # Never merge through a math group boundary into the next text node.
    assert _merge_text_runs(r'\frac{x}{y}\text{甲}\text{乙}') == r'\frac{x}{y}\text{甲乙}'


@pytest.mark.parametrize('kind', ['skw', 'noBar', 'unknown'])
def test_fraction_types_are_not_silently_coerced(kind):
    result = convert_omml(formula(f'<m:f><m:fPr><m:type m:val="{kind}"/></m:fPr>'
                                  '<m:num><m:r><m:t>1</m:t></m:r></m:num>'
                                  '<m:den><m:r><m:t>2</m:t></m:r></m:den></m:f>'))
    assert result['status'] == 'review'


def test_known_tag_in_wrong_position_cannot_disappear_next_to_valid_content():
    result = convert_omml(formula('<m:r><m:t>x</m:t></m:r><m:num><m:r><m:t>y</m:t></m:r></m:num>'))
    assert result['status'] == 'review'
    assert result['latex'] is None


def test_literal_tilde_is_not_converted_to_tex_spacing():
    result = convert_omml(formula('<m:r><m:t>0~0.6A</m:t></m:r>'))
    assert result['status'] == 'converted'
    assert result['latex'] == r'0\sim 0.6A'


def test_linear_fraction_keeps_slash_and_compound_operand_precedence():
    result = convert_omml(formula('<m:f><m:fPr><m:type m:val="lin"/></m:fPr>'
        '<m:num><m:r><m:t>a+b</m:t></m:r></m:num>'
        '<m:den><m:r><m:t>c+d</m:t></m:r></m:den></m:f>'))
    assert result['status'] == 'converted'
    assert result['latex'] == r'\left(a+b\right)/\left(c+d\right)'
    assert r'\frac' not in result['latex']


def test_array_bars_preserve_source_order_and_direction_without_invented_reaction():
    result = convert_omml(formula('<m:eqArr><m:e><m:bar><m:barPr><m:pos m:val="bot"/></m:barPr>'
        '<m:e><m:r><m:t>催化剂</m:t></m:r></m:e></m:bar></m:e>'
        '<m:e><m:r><m:t>x</m:t></m:r></m:e></m:eqArr>'))
    assert result['status'] == 'converted'
    assert r'\underline{催化剂}' in result['latex']
    assert result['latex'].index('催化剂') < result['latex'].index('x')
    assert r'\rightarrow' not in result['latex'] and '=' not in result['latex']


def test_real_newly_supported_omml_cases_preserve_content():
    source = REFERENCE / '微软公式物理.docx'
    if not source.exists():
        pytest.skip('Reference fixtures not present')
    with ZipFile(source) as archive:
        root = ET.fromstring(archive.read('word/document.xml'))
    formulas = list(root.iter(f'{{{NS}}}oMath'))
    assert convert_omml(ET.tostring(formulas[86]))['latex'] == r'{I}/{\text{A}}'
    assert convert_omml(ET.tostring(formulas[315]))['latex'] == r'0\sim 0.6\text{A}'
    with ZipFile(REFERENCE / '微软公式化学.docx') as archive:
        root = ET.fromstring(archive.read('word/document.xml'))
    result = convert_omml(ET.tostring(list(root.iter(f'{{{NS}}}oMath'))[3]))
    assert result['status'] == 'converted'
    assert r'\overline{\overline{点燃}}' in result['latex']
    assert r'\rightarrow' not in result['latex']


def test_unknown_array_property_and_bar_direction_still_fail_closed():
    for body in [
        '<m:eqArr><m:eqArrPr><m:unknown/></m:eqArrPr><m:e><m:r><m:t>x</m:t></m:r></m:e></m:eqArr>',
        '<m:bar><m:barPr><m:pos m:val="sideways"/></m:barPr><m:e><m:r><m:t>x</m:t></m:r></m:e></m:bar>',
    ]:
        assert convert_omml(formula(body))['status'] == 'review'


def test_real_mathtype_double_underbar_keeps_count_direction_and_empty_row():
    source = REFERENCE / 'MathType公式化学2.docx'
    if not source.exists():
        pytest.skip('Reference fixtures not present')
    with ZipFile(source) as archive:
        result = convert_mathtype(archive.read('word/embeddings/oleObject4.bin'))
    assert result['status'] == 'converted'
    assert result['latex'] == r'4P+5O_2\begin{array}{l}\underline{\underline{\text{点燃}}} \\ {}\end{array}2P_2O_5'
    assert '=' not in result['latex'] and r'\rightarrow' not in result['latex']
    assert r'\overline' not in result['latex']


def test_mtef_matrix_subset_rejects_unknown_alignment_partitions_and_extra_cells():
    bar = mtef.Tmpl(12, 1, 0, [mtef.Line([mtef.Text('x', mtcode=120, typeface=3)])])
    bar.source_options = 0
    stack = mtef.Mtx(2, 1, [mtef.Line([bar]), mtef.Line([])])
    stack.source_alignment = (1, 1, 1)
    stack.source_partitions = b'\0\0'
    reasons = []
    _audit_mtef(stack, reasons)
    assert not reasons
    for attribute, value in [('source_alignment', (1, 130, 129)),
                             ('source_partitions', b'\1\0'), ('cols', 2)]:
        original = getattr(stack, attribute)
        setattr(stack, attribute, value)
        reasons = []
        _audit_mtef(stack, reasons)
        assert reasons
        setattr(stack, attribute, original)


@pytest.mark.parametrize('selector,variation,expected', [
    (12, 0, r'\underline{x}'), (12, 1, r'\underline{\underline{x}}'),
    (13, 0, r'\overline{x}'), (13, 1, r'\overline{\overline{x}}'),
])
def test_mtef_bar_adapter_does_not_ignore_double_bit(selector, variation, expected):
    from qa_parser.formulas import _adapt_mtef_tree
    bar = mtef.Tmpl(selector, variation, 0, [mtef.Line([mtef.Text('x', mtcode=120, typeface=3)])])
    bar.source_options = 0
    reasons = []
    _audit_mtef(bar, reasons)
    assert not reasons
    assert mtef.to_latex(_adapt_mtef_tree(bar)) == expected
