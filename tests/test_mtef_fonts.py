"""Raw-record and source-object checks for the narrow explicit font adapter."""
import io
from pathlib import Path
from zipfile import ZipFile

import olefile
import pytest

from qa_parser.vendor import mtef
from qa_parser.mtef_fonts import FontMetadataMixin, is_plain_unicode_custom_font, plain_custom_font_latex


class Parser(FontMetadataMixin, mtef.MTEFParser):
    pass


def custom(s, *, typeface=-1, name=b'Times New Roman', style=0):
    node = mtef.Text(s, typeface=typeface, mtcode=ord(s))
    node.source_font = {'name': name, 'style': style, 'encoding': 5, 'font_index': 1}
    return node


@pytest.mark.parametrize('value,font,expected', [
    ('↑', b'Symbol', r'\uparrow'), ('Δ', b'Malgun Gothic', r'\Delta'),
    ('∶', b'Batang', r'\mathrel{:}'), ('氢', b'\xcb\xce\xcc\xe5', r'\text{氢}'),
    ('A', b'Times New Roman', r'\mathrm{A}'),
])
def test_explicit_unicode_plain_font_characters(value, font, expected):
    assert plain_custom_font_latex(custom(value, name=font)) == expected


@pytest.mark.parametrize('mutation', [
    lambda n: setattr(n, 'mtcode', None),
    lambda n: setattr(n, 'mtcode', 0xE000),
    lambda n: setattr(n, 's', 'B'),
    lambda n: setattr(n, 'source_font', None),
    lambda n: n.source_font.__setitem__('name', b'Unknown Blackboard'),
    lambda n: n.source_font.__setitem__('style', 1),
    lambda n: n.source_font.__setitem__('style', 2),
    lambda n: setattr(n, 'typeface', 7),
    lambda n: setattr(n, 'embell', [11]),
])
def test_unknown_or_semantically_styled_fonts_still_fail_closed(mutation):
    node = custom('A')
    mutation(node)
    assert not is_plain_unicode_custom_font(node)
    assert plain_custom_font_latex(node) is None


def test_font_style_record_is_bound_at_character_read_time():
    # FONT_DEF #1 Times New Roman; FONT_STYLE_DEF #1 font1/plain;
    # LINE: CHAR(typeface=-1,MTCode='A'); END; END.
    raw = b'\x11\x05Times New Roman\0\x08\x01\x00\x01\x00\x02\x00\x7fA\0\0\0'
    node = Parser(raw).parse().items[0]
    assert node.source_font == {'name': b'Times New Roman', 'encoding': 5,
                                'style': 0, 'font_index': 1}
    assert plain_custom_font_latex(node) == r'\mathrm{A}'


def test_missing_explicit_definition_cannot_be_guessed_from_first_font():
    raw = b'\x11\x05Times New Roman\0\x01\x00\x02\x00\x7fA\0\0\0'
    node = Parser(raw).parse().items[0]
    assert node.source_font is None
    assert not is_plain_unicode_custom_font(node)


def walk(node):
    yield node
    for attribute in ('items', 'slots', 'lines', 'cells'):
        for child in getattr(node, attribute, []):
            yield from walk(child)


@pytest.mark.parametrize('filename,part,expected', [
    ('MathType公式化学2.docx', 28, list('氢气氧气点燃水')),
    ('MathType公式化学填空2.docx', 170, ['↑', '↑']),
    ('MathType公式化学填空2.docx', 232, ['Δ']),
    ('MathType公式物理.docx', 601, ['A']),
    ('MathType公式物理.docx', 654, ['∶']),
    ('MathType公式物理.docx', 655, ['∶']),
    ('MathType公式物理.docx', 656, ['∶']),
])
def test_seven_original_objects_resolve_their_actual_font_definitions(filename, part, expected):
    path = Path(__file__).resolve().parents[1] / 'reference' / filename
    if not path.exists():
        pytest.skip('Reference fixtures not present')
    with ZipFile(path) as doc:
        with olefile.OleFileIO(io.BytesIO(doc.read(f'word/embeddings/oleObject{part}.bin'))) as ole:
            raw = ole.openstream('Equation Native').read()
    from qa_parser.formulas import _StrictMTEFParser
    parser = _StrictMTEFParser(raw[raw.find(b'\0', 33) + 2:])
    tree = parser.parse()
    nodes = [n for n in walk(tree) if isinstance(n, mtef.Text)
             and (n.typeface < 0 or n.typeface in (7, 9, 10))]
    assert [n.s for n in nodes] == expected
    assert all(is_plain_unicode_custom_font(n) for n in nodes)
    assert all(n.source_font['style'] == 0 for n in nodes)
    assert parser.r.d[parser.r.i:] == b'\0'
