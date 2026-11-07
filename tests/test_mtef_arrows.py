"""Condition arrows: exact source MTEF fixtures and structural rejection checks."""
from pathlib import Path
from zipfile import ZipFile

import pytest

from qa_parser.formulas import _adapt_mtef_tree, _audit_mtef, convert_mathtype
from qa_parser.vendor import mtef

REFERENCE = Path(__file__).resolve().parents[1] / 'reference'


def char(value):
    return mtef.Text(value, mtcode=ord(value), typeface=12 if ord(value) > 127 else 1)


def line(value, *, null=False):
    return mtef.Line([char(c) for c in value], null=null)


def arrow(variation, upper='', lower='', glyph='→'):
    node = mtef.Tmpl(14, variation, 0, [line(upper, null=not bool(variation & 4)),
                                      line(lower, null=not bool(variation & 8)), char(glyph)])
    node.slots[2].typeface = 22
    node.source_options = 0
    return node


def render(node):
    reasons = []
    _audit_mtef(node, reasons)
    assert not reasons, reasons
    return mtef.to_latex(_adapt_mtef_tree(node))


@pytest.mark.parametrize('variation,upper,lower,glyph,expected', [
    (0x24, '点燃', '', '→', r'\xrightarrow{\text{点燃}}'),
    (0x2c, '点燃', '', '→', r'\xrightarrow{\text{点燃}}'),
    (0x24, 'Δ', '', '→', r'\xrightarrow{\Delta}'),
    (0x2c, '高温', '催化剂', '→', r'\xrightarrow[\text{催化剂}]{\text{高温}}'),
    (0x28, '', '加热', '→', r'\xrightarrow[\text{加热}]{}'),
    (0x14, '点燃', '', '←', r'\xleftarrow{\text{点燃}}'),
    (0x1c, '高温', '催化剂', '←', r'\xleftarrow[\text{催化剂}]{\text{高温}}'),
    (0x18, '', '加热', '←', r'\xleftarrow[\text{加热}]{}'),
])
def test_condition_arrows_keep_direction_and_condition_side(variation, upper, lower, glyph, expected):
    assert render(arrow(variation, upper, lower, glyph)) == expected


@pytest.mark.parametrize('mutation', [
    lambda n: setattr(n, 'variation', 0x04),  # no direction
    lambda n: setattr(n, 'variation', 0x34),  # both heads unsupported
    lambda n: setattr(n, 'variation', 0x25),  # double arrow not silently made single
    lambda n: setattr(n, 'variation', 0x64),  # unsupported high bit
    lambda n: setattr(n, 'source_options', 1),
    lambda n: n.slots.append(line('extra')),
    lambda n: n.slots.pop(),
    lambda n: n.slots.__setitem__(2, char('←')),  # glyph disagrees with right flag
    lambda n: n.slots.__setitem__(2, char('x')),
    lambda n: n.slots.__setitem__(1, line('hidden')),  # absent lower flag cannot hide content
    lambda n: n.slots.__setitem__(0, char('x')),  # label slot must be LINE
    lambda n: n.slots[0].items.append(mtef.Tmpl(99, 0, 0, [])),
])
def test_ambiguous_or_malformed_condition_arrows_remain_review(mutation):
    node = arrow(0x24, '点燃')
    mutation(node)
    reasons = []
    _audit_mtef(node, reasons)
    assert reasons


def from_source(filename, number):
    source = REFERENCE / filename
    if not source.exists():
        pytest.skip('Reference fixtures not present')
    with ZipFile(source) as archive:
        return convert_mathtype(archive.read(f'word/embeddings/oleObject{number}.bin'))


@pytest.mark.parametrize('number,expected', [
    (1, r'\xrightarrow{\text{点燃}}'),
    (2, r'\xrightarrow{\text{点燃}}'),
    (3, r'\xrightarrow{\Delta}'),
])
def test_real_question_one_raw_ole_conditions_match_source(number, expected):
    result = from_source('MathType公式化学2.docx', number)
    assert result['status'] == 'converted', result
    assert result['latex'] == expected


@pytest.mark.parametrize('filename,number,expected', [
    ('MathType公式化学2.docx', 14, r'\xrightarrow{\text{二氧化锰}}'),
    ('MathType公式化学2.docx', 29, r'\xrightarrow{\text{高温}}'),
    ('MathType公式化学2.docx', 87, r'\xrightarrow{\text{加热}}'),
    ('MathType公式化学填空.docx', 66, r'\xrightarrow{T_1{}^{\circ}\mathrm{C}}'),
    ('MathType公式化学填空.docx', 67, r'\xrightarrow{T_2{}^{\circ}\mathrm{C}}'),
    ('MathType公式化学填空.docx', 68, r'\xrightarrow{T_3{}^{\circ}\mathrm{C}}'),
    ('MathType公式化学填空2.docx', 107, r'\xrightarrow{MnO_2}'),
])
def test_real_conditions_preserve_complete_text_and_nested_scripts(filename, number, expected):
    result = from_source(filename, number)
    assert result['status'] == 'converted', result
    assert expected in result['latex']


# Fixed inventory taken from the original nine DOCX ZIPs; no exported text is used.
ARROW_PARTS = [
    ('MathType公式化学2.docx', number)
    for number in (1, 2, 3, 8, 9, 14, 15, 16, 17, 18, 27, 28, 29,
                   37, 38, 39, 40, 41, 42, 43, 44, 87, 88, 89, 90)
] + [('MathType公式化学填空.docx', number) for number in (66, 67, 68)] + [
    ('MathType公式化学填空2.docx', number) for number in (106, 107)]


@pytest.mark.parametrize('filename,number', ARROW_PARTS)
def test_all_thirty_original_arrow_objects(filename, number):
    result = from_source(filename, number)
    assert result['status'] == 'converted', result
    assert r'\xrightarrow' in result['latex']
    if filename == 'MathType公式化学2.docx' and number == 28:
        # Independently resolved FONT_STYLE_DEF now proves plain 宋体 with
        # explicit Unicode; tests/test_mtef_fonts.py covers that binding.
        for character in '氢气氧气点燃水':
            assert character in result['latex']
