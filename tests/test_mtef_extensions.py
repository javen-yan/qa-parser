"""Real MTEF regressions for recovered structure, plus malformed variants."""
import copy
from pathlib import Path
from zipfile import ZipFile

import pytest

from qa_parser import mtef_extensions as ext
from qa_parser.vendor import mtef

REFERENCE = Path(__file__).resolve().parents[1] / 'reference'


def text(value, typeface=3):
    return mtef.Text(value, typeface=typeface, mtcode=ord(value))


def test_color_operand_is_not_a_prime_and_true_prime_is_preserved():
    # typeface3 't', COLOR index0, EMBELL no-options prime5, END.
    parser = mtef.MTEFParser(bytes.fromhex('8374000f0006000500'))
    node = ext.read_char(parser, mtef.OPT_CHAR_EMBELL)
    assert node.s == 't' and node.mtcode == 116
    assert node.embell == [5]
    assert node.latex() == "{t'}"
    assert parser.r.eof()


@pytest.mark.parametrize('suffix', ['06000000', '0f0106000500', '020083780000', '06020500'])
def test_unknown_embell_is_never_erased_or_guessed(suffix):
    parser = mtef.MTEFParser(bytes.fromhex('837400' + suffix))
    if suffix == '06000000':
        assert ext.read_char(parser, mtef.OPT_CHAR_EMBELL).embell == [0]
    else:
        with pytest.raises(mtef.MTEFError):
            ext.read_char(parser, mtef.OPT_CHAR_EMBELL)


def test_tex_input_history_is_recognized_by_envelope_not_used_as_content():
    assert ext.is_tex_input_metadata(102, b'TeX Input Language\0stale source\0')
    for record, payload in [(103, b'TeX Input Language\0x\0'),
                            (102, b'Unknown Meaning\0x\0'),
                            (102, b'TeX Input Language\0x'),
                            (102, b'TeX Input Language\0x\0hidden\0')]:
        assert not ext.is_tex_input_metadata(record, payload)


@pytest.mark.parametrize('variation,code,command', [
    (1, 0x20D6, r'\overleftarrow'), (2, 0x20D7, r'\overrightarrow'),
    (5, 0x20D6, r'\underleftarrow'), (6, 0x20D7, r'\underrightarrow'),
])
def test_vector_keeps_direction_and_above_below(variation, code, command):
    vector = mtef.Tmpl(31, variation, 0, [mtef.Line([text('x')]), text(chr(code), 22)])
    vector.source_options = 0
    reasons = []
    assert ext.audit_node(vector, reasons) and not reasons
    assert ext.render_node(vector) == command + '{x}'
    vector.slots[1].mtcode = 0x20D6 if code == 0x20D7 else 0x20D7
    assert ext.audit_node(vector, reasons) and reasons


def test_matrix_preserves_rows_columns_and_empty_cell():
    node = mtef.Mtx(2, 2, [mtef.Line([text('a')]), mtef.Line([]),
                           mtef.Line([text('b')]), mtef.Line([text('c')])])
    node.source_alignment = (1, 1, 1)
    node.source_partitions = b'\0\0'
    reasons = []
    assert ext.audit_node(node, reasons) and not reasons
    assert ext.render_node(node) == r'\begin{array}{ll}a & {} \\ b & c\end{array}'
    for attr, value in [('source_alignment', (1, 129, 129)),
                        ('source_partitions', b'\1\0'), ('cells', node.cells[:-1])]:
        bad = copy.deepcopy(node)
        setattr(bad, attr, value)
        reasons = []
        assert ext.audit_node(bad, reasons) and reasons


def test_pile_keeps_empty_middle_row_and_alignment():
    pile = mtef.Pile([mtef.Line([text('a')]), mtef.Line([]), mtef.Line([text('b')])], 3, 1)
    pile.source_options = 0
    reasons = []
    assert ext.audit_node(pile, reasons) and not reasons
    assert ext.render_node(pile) == r'\begin{array}{r}a \\ {} \\ b\end{array}'
    pile.source_options = 2  # Ruler metadata cannot silently disappear.
    assert ext.audit_node(pile, reasons) and reasons


def fixture(name, part):
    source = REFERENCE / name
    if not source.exists():
        pytest.skip('Reference fixtures unavailable')
    with ZipFile(source) as archive:
        return archive.read('word/embeddings/' + part + '.bin')


def test_real_color_before_prime_and_cached_source_convert_from_structure():
    from qa_parser.formulas import convert_mathtype
    out = convert_mathtype(fixture('MathType公式物理2.docx', 'oleObject196'))
    assert out['status'] == 'converted', out
    assert "{t'}" in out['latex'] and r'\frac' in out['latex']
    out = convert_mathtype(fixture('MathType公式化学3.docx', 'oleObject39'))
    assert out['status'] == 'converted', out
    assert out['latex'] == 'U_L=2.5V'


def test_real_condition_vector_and_nonempty_lower_row_remain_in_place():
    from qa_parser.formulas import convert_mathtype
    out = convert_mathtype(fixture('MathType公式化学2.docx', 'oleObject10'))
    assert out['status'] == 'converted', out
    assert r'\underrightarrow{' in out['latex']
    assert '点燃' in out['latex'].replace('}{', '')
    out = convert_mathtype(fixture('MathType公式化学填空2.docx', 'oleObject5'))
    assert out['status'] == 'converted', out
    assert r'\underline{\underline{' in out['latex']
    assert r'\\ \Delta' in out['latex']
    assert '催化剂' in out['latex'].replace('}{', '')


def test_limit_template_preserves_main_lower_upper_instead_of_inventing_lim():
    node = mtef.Tmpl(23, 0x30, 0, [mtef.Line([text('x')]),
                                  mtef.Line([text('b')]), mtef.Line([text('a')])])
    node.source_options = 0
    reasons = []
    assert ext.audit_node(node, reasons) and not reasons
    assert ext.render_node(node) == r'\overset{a}{\underset{b}{x}}'
    node.variation = 0x20
    reasons = []
    assert ext.audit_node(node, reasons) and reasons  # Can't hide nonempty lower.


def test_prescript_keeps_next_base_and_rejects_no_base():
    from qa_parser.formulas import _audit_mtef, _adapt_mtef_tree
    pre = mtef.Tmpl(28, 1, 0, [mtef.Line([], null=True), mtef.Line([text('2')])])
    pre.source_options = 0
    line = mtef.Line([text('I'), pre, text('R'), text('t')])
    reasons = []
    _audit_mtef(line, reasons)
    assert not reasons
    assert mtef.to_latex(_adapt_mtef_tree(line)) == 'I{}^{2}Rt'
    standalone = copy.deepcopy(pre)
    standalone.source_precedes_valid = False
    reasons = []
    assert ext.audit_node(standalone, reasons) and reasons


@pytest.mark.parametrize('code,expected', [(0xEF01, r'\hspace{0pt}'), (0xEF02, r'\,'),
                                          (0xEF04, r'\;'), (0xEF08, r'\hspace{1pt}')])
def test_named_spacing_codes_require_spacing_typeface(code, expected):
    node = text(chr(code), 24)
    reasons = []
    assert ext.audit_node(node, reasons) and not reasons
    assert ext.render_node(node) == expected
    node.typeface = 22
    assert not ext.audit_node(node, [])
    node.typeface = 24
    node.embell = [5]
    assert ext.audit_node(node, reasons) and reasons


def test_real_limit_oxidation_condition_and_pre_script():
    from qa_parser.formulas import convert_mathtype
    out = convert_mathtype(fixture('MathType公式化学填空.docx', 'oleObject53'))
    assert out['status'] == 'converted', out
    assert out['latex'] == r'\overset{+2}{Mg}O'
    out = convert_mathtype(fixture('MathType公式化学填空2.docx', 'oleObject243'))
    assert out['status'] == 'converted', out
    assert r'\overset{' in out['latex'] and r'\to' in out['latex']
    assert '光照' in out['latex']
    out = convert_mathtype(fixture('MathType公式物理2.docx', 'oleObject26'))
    assert out['status'] == 'converted', out
    assert out['latex'] == 'Q=I{}^{2}Rt'


def test_matrix_cell_can_be_a_structured_pile_without_losing_its_rows():
    from qa_parser.formulas import _audit_mtef, _adapt_mtef_tree
    pile = mtef.Pile([mtef.Line([text('a')]), mtef.Line([text('b')])], 2, 1)
    pile.source_options = 0
    node = mtef.Mtx(1, 2, [mtef.Line([text('x')]), pile])
    node.source_alignment = (1, 1, 1)
    node.source_partitions = b'\0\0'
    reasons = []
    _audit_mtef(node, reasons)
    assert not reasons
    assert mtef.to_latex(_adapt_mtef_tree(node)) == (
        r'\begin{array}{ll}x & \begin{array}{c}a \\ b\end{array}\end{array}')
