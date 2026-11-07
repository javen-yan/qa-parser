"""Reader regression tests check preservation, not just successful parsing."""
from pathlib import Path
from zipfile import ZipFile
import re

import pytest

from qa_parser.document import read_document, NS

PNG = b'\x89PNG\r\n\x1a\n' + b'example-image'


def docx(tmp_path, body, relationships='', assets=None):
    path = tmp_path / '含 空格.docx'
    attrs = ' '.join(f'xmlns:{prefix}="{uri}"' for prefix, uri in NS.items())
    xml = f'<w:document {attrs}><w:body>{body}</w:body></w:document>'
    with ZipFile(path, 'w') as z:
        z.writestr('word/document.xml', xml)
        z.writestr('word/_rels/document.xml.rels', '<Relationships>' + relationships + '</Relationships>')
        for name, data in (assets or {}).items():
            z.writestr(name, data)
    return path


def flattened(result):
    return [t for block in result['blocks'] for t in block['tokens']]


def text(result):
    return ''.join(t['text'] for t in flattened(result) if t['kind'] == 'text')


def test_body_table_sequence_and_cell_boundaries(tmp_path):
    path = docx(tmp_path, '''<w:p><w:r><w:t>1．题目</w:t></w:r></w:p>
      <w:tbl><w:tr><w:tc><w:p><w:r><w:t>A．甲</w:t></w:r></w:p></w:tc>
      <w:tc><w:p><w:r><w:t>B．乙</w:t></w:r></w:p><w:p><w:r><w:t>续</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
      <w:p><w:r><w:t>【答案】A</w:t></w:r></w:p>''')
    result = read_document(path)
    assert [b['kind'] for b in result['blocks']] == ['paragraph', 'table_row', 'paragraph']
    assert text(result) == '1．题目A．甲\tB．乙\n续【答案】A'
    assert len(result['blocks'][1]['cells']) == 2
    assert result['blocks'][1]['cells'][0]['token_end'] == 1
    assert '<w:tr' in result['blocks'][1]['source_xml']


def test_mathtype_keeps_ole_and_preview_without_duplicate_image(tmp_path):
    path = docx(tmp_path, '''<w:p><w:r><w:t>前</w:t></w:r><w:r><w:object>
      <v:shape><v:imagedata r:id="preview"/></v:shape>
      <o:OLEObject ProgID="Equation.DSMT4" r:id="equation"/>
      </w:object></w:r><w:r><w:t>后</w:t></w:r></w:p>''',
      '<Relationship Id="preview" Target="media/formula.png"/>'
      '<Relationship Id="equation" Target="embeddings/formula.bin"/>',
      {'word/media/formula.png': PNG, 'word/embeddings/formula.bin': b'OLE data'})
    tokens = flattened(read_document(path))
    assert [t['kind'] for t in tokens] == ['text', 'formula', 'text']
    assert tokens[1]['format'] == 'mathtype'
    assert tokens[1]['data'] == b'OLE data'
    assert tokens[1]['preview'] == PNG
    assert tokens[1]['preview_ext'] == '.png'
    assert 'OLEObject' in tokens[1]['location']


def test_omml_is_one_token_and_preserves_xml(tmp_path):
    path = docx(tmp_path, '<w:p><m:oMath><m:f><m:num><m:r><m:t>1</m:t></m:r></m:num>'
                        '<m:den><m:r><m:t>2</m:t></m:r></m:den></m:f></m:oMath></w:p>')
    tokens = flattened(read_document(path))
    assert len(tokens) == 1
    assert tokens[0]['format'] == 'omml'
    assert b'<m:den>' in tokens[0]['data']


def test_superscript_underlined_whitespace_and_breaks(tmp_path):
    path = docx(tmp_path, '''<w:p><w:r><w:t>H</w:t></w:r><w:r><w:rPr><w:vertAlign w:val="subscript"/></w:rPr><w:t>2</w:t></w:r>
      <w:r><w:rPr><w:u w:val="single"/></w:rPr><w:t xml:space="preserve">   </w:t></w:r>
      <w:r><w:tab/><w:br/></w:r></w:p>''')
    tokens = flattened(read_document(path))
    assert tokens[0]['data'] == b'H_{2}'
    assert tokens[0]['origin'] == 'word_script_sequence'
    assert tokens[1]['text'] == '   ' and tokens[1]['underline'] is True
    assert tokens[2]['text'] == '\t' and tokens[3]['text'] == '\n'


def test_alternate_content_uses_only_supported_branch(tmp_path):
    path = docx(tmp_path, '''<w:p><mc:AlternateContent><mc:Choice Requires="a">
      <w:r><w:drawing><a:blip r:embed="picture"/></w:drawing></w:r></mc:Choice>
      <mc:Fallback><w:r><w:pict><v:shape><v:imagedata r:id="picture"/></v:shape></w:pict></w:r></mc:Fallback>
      </mc:AlternateContent></w:p>''',
      '<Relationship Id="picture" Target="media/p.png"/>', {'word/media/p.png': PNG})
    assert len(flattened(read_document(path))) == 1


@pytest.mark.parametrize('rel', [
    '<Relationship Id="pic" Target="https://example.invalid/a.png" TargetMode="External"/>',
    '<Relationship Id="pic" Target="media/missing.png"/>',
    '<Relationship Id="pic" Target="../../outside.png"/>',
    '',
])
def test_missing_external_unsafe_resources_become_review_tokens(tmp_path, rel):
    path = docx(tmp_path, '<w:p><w:r><w:drawing><a:blip r:embed="pic"/></w:drawing></w:r></w:p>', rel)
    result = read_document(path)
    assert flattened(result)[0]['kind'] == 'unsupported'
    assert result['issues'][0]['severity'] == 'review'


def test_unknown_xml_is_not_silently_dropped(tmp_path):
    path = docx(tmp_path, '<w:p><w:r><w:madeUp/></w:r></w:p>')
    result = read_document(path)
    assert flattened(result)[0]['kind'] == 'unsupported'


def test_unsafe_zip_entry_rejected(tmp_path):
    path = docx(tmp_path, '', assets={'../escape': b'x'})
    with pytest.raises(ValueError, match='unsafe'):
        read_document(path)


def test_doctype_rejected(tmp_path):
    path = tmp_path / 'bad.docx'
    with ZipFile(path, 'w') as z:
        z.writestr('word/document.xml', '<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>')
    with pytest.raises(ValueError, match='document type'):
        read_document(path)


SAMPLES = Path(__file__).resolve().parents[1] / 'reference'


@pytest.mark.parametrize('path', sorted(SAMPLES.glob('*.docx')), ids=lambda p: p.stem)
def test_reference_question_boundaries_and_resource_accounting(path):
    result = read_document(path)
    # Explicit run scripts inside balanced chemical groups are now retained.
    assert result['issues'] == []
    # Numbered question markers remain in order; formulas do not erase labels.
    numbers = []
    for block in result['blocks']:
        value = ''.join(t['text'] for t in block['tokens'] if t['kind'] == 'text')
        match = re.match(r'^(\d+)．', value)
        if match:
            numbers.append(int(match[1]))
    assert numbers == list(range(1, 101))
    tokens = flattened(result)
    assert sum('【答案】' in t.get('text', '') for t in tokens) == 100
    assert all(t.get('location') for t in tokens)
    images = [t for t in tokens if t['kind'] == 'image']
    expected = {'MathType公式化学': 170, 'MathType公式化学2': 128,
                'MathType公式化学3': 124, 'MathType公式化学填空': 61,
                'MathType公式化学填空2': 69, 'MathType公式物理': 78,
                'MathType公式物理2': 75, '微软公式化学': 128, '微软公式物理': 124}
    assert len(images) == expected[path.stem]
    assert all(t['data'] for t in images)


def test_chemical_and_consecutive_scripts_share_a_real_base(tmp_path):
    path = docx(tmp_path, '<w:p><w:r><w:t>答案Fe</w:t></w:r>'
        '<w:r><w:rPr><w:vertAlign w:val="subscript"/></w:rPr><w:t>2</w:t></w:r>'
        '<w:r><w:t>O</w:t></w:r>'
        '<w:r><w:rPr><w:vertAlign w:val="subscript"/></w:rPr><w:t>3</w:t></w:r>'
        '<w:r><w:t>，R</w:t></w:r>'
        '<w:r><w:rPr><w:vertAlign w:val="subscript"/></w:rPr><w:t>x</w:t></w:r>'
        '<w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:t>2</w:t></w:r></w:p>')
    result = read_document(path)
    tokens = flattened(result)
    assert not result['issues']
    assert [t['data'] for t in tokens if t['kind'] == 'formula'] == [b'Fe_{2}O_{3}', b'R_{x}^{2}']
    assert text(result) == '答案，'
    assert '<w:vertAlign' in result['blocks'][0]['source_xml']


def test_orphan_script_is_review_not_success(tmp_path):
    path = docx(tmp_path, '<w:p><w:r><w:t>中文</w:t></w:r>'
        '<w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:t>2</w:t></w:r></w:p>')
    result = read_document(path)
    token = flattened(result)[1]
    assert token['data'] == b'{}^{2}'
    assert token['review_reason']
    assert result['issues'][0]['code'] == 'word_script'


def test_symbol_font_and_field_code_require_review_with_cached_text(tmp_path):
    path = docx(tmp_path, '<w:p><w:r><w:rPr><w:rFonts w:ascii="Symbol"/></w:rPr><w:t>a</w:t></w:r>'
        '<w:fldSimple w:instr="EQ something"><w:r><w:t>cached result</w:t></w:r></w:fldSimple></w:p>')
    result = read_document(path)
    assert {i['code'] for i in result['issues']} == {'symbol_font', 'field_code'}
    assert text(result) == 'acached result'
