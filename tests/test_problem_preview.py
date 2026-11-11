"""Customer explanations preserve evidence without rendering rejected objects."""
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

from PIL import Image
import pytest

from qa_parser import core
from qa_parser.config import load_config
from qa_parser.problem_preview import build_problem_preview, render_preview_html, _Images
from test_auto import docx, paragraph, text, options, ole, valid_question, PNG
from test_formula_images import wmf


def failed_adapter(monkeypatch):
    def convert(data):
        return {'latex': None, 'status': 'review', 'reason': '未验证的 MTEF 模板: selector=14, variation=44', 'engine': 'test'}
    monkeypatch.setattr('qa_parser.formulas.convert_mathtype', convert)


def test_script_position_is_deduplicated_and_original_digit_preserved(tmp_path):
    script = '<w:r><w:rPr><w:vertAlign w:val="subscript"/></w:rPr><w:t>2</w:t></w:r>'
    body = '<w:p>' + text('1．前文（') + script + text('）后文') + '</w:p>' + options()
    source = docx(tmp_path / '下标.docx', body)
    task = tmp_path / 'task'
    core.parse_document(source, 'choice', task)
    before = (task / 'task.json').read_bytes()
    preview = build_problem_preview(task)
    assert len(preview['questions']) == 1
    q = preview['questions'][0]
    assert len(q['problems']) == 1
    p = q['problems'][0]
    assert p['field'] == 'stem' and p['category'] == 'structure'
    assert 'word_script' in p['diagnostic'] and 'formula_review' in p['diagnostic']
    blocked = [s for s in q['fields'][0]['segments'] if s['problem_ids']]
    assert len(blocked) == 1 and '独立下标“2”' in blocked[0]['text']
    assert blocked[0]['type'] == 'placeholder'
    assert (task / 'task.json').read_bytes() == before


def test_multiple_formula_fields_keep_original_raster_but_never_guess_text(tmp_path, monkeypatch):
    failed_adapter(monkeypatch)
    body = paragraph('1．题干') + '<w:p>' + text('A．前') + ole() + text('后 B．乙 C．丙 D．丁') + '</w:p>'
    body += paragraph('【答案】A') + '<w:p>' + text('【解析】原文') + ole() + '</w:p>'
    task = tmp_path / 'task'
    config = load_config(); config['formula_fallback'] = 'isolate'
    core.parse_document(docx(tmp_path / 'a.docx', body), 'choice', task, config)
    view = build_problem_preview(task)
    q = view['questions'][0]
    assert {p['field'] for p in q['problems']} == {'options.A', 'analysis'}
    images = [s for f in q['fields'] for s in f['segments'] if s['type'] == 'image']
    assert len(images) == 2
    assert all(s['src'].startswith('data:image/png;base64,') and s['problem_ids'] for s in images)
    assert all('未成功转换' in s['alt'] for s in images)
    assert not any(s['type'] == 'latex' for f in q['fields'] for s in f['segments'])


def test_rejected_wmf_is_placeholder_and_never_calls_renderer(tmp_path, monkeypatch):
    failed_adapter(monkeypatch)
    source = docx(tmp_path / 'a.docx', '<w:p>' + text('1．前') + ole() + text('后') + '</w:p>' + options())
    # Replace the fixture package's preview with a real Symbol WMF.
    with ZipFile(source) as z:
        items = {name: z.read(name) for name in z.namelist()}
    items['word/_rels/document.xml.rels'] = items['word/_rels/document.xml.rels'].replace(b'preview.png', b'preview.wmf')
    del items['word/media/preview.png']
    items['word/media/preview.wmf'] = wmf('Symbol', b'A')
    with ZipFile(source, 'w') as z:
        for name, content in items.items(): z.writestr(name, content)
    task = tmp_path / 'task'; core.parse_document(source, 'choice', task)
    monkeypatch.setattr('qa_parser.formula_images.preview_to_png', lambda *_: pytest.fail('Rejected WMF must not be rendered by preview'))
    view = build_problem_preview(task)
    q = view['questions'][0]
    assert q['problems'][0]['category'] == 'fidelity'
    assert 'symbol' in q['problems'][0]['reason']
    assert '模板' in q['problems'][0]['reason']
    assert '具体哪个符号' in q['problems'][0]['reason']
    assert any(s['type'] == 'placeholder' and s['problem_ids'] for f in q['fields'] for s in f['segments'])
    assert not any(s['type'] == 'image' for f in q['fields'] for s in f['segments'])


def test_no_problem_file_and_unreadable_task_have_different_empty_states(tmp_path):
    task = tmp_path / 'task'
    core.parse_document(docx(tmp_path / 'good.docx', valid_question('1')), 'choice', task)
    good = build_problem_preview(task)
    assert good['questions'] == [] and good['document_issues'] == []
    assert '没有未导出' in good['empty_message']
    missing = build_problem_preview(tmp_path / 'missing', '损坏文件.docx')
    assert missing['questions'] == [] and missing['document_issues']
    assert missing['source_name'] == '损坏文件.docx' and '无法读取' in missing['empty_message']


def test_preview_raster_limits_hash_and_paths(tmp_path):
    inside = tmp_path / 'image.png'; inside.write_bytes(PNG)
    metadata = {'path': 'image.png', 'sha256': hashlib.sha256(PNG).hexdigest()}
    images = _Images(tmp_path)
    assert images.load(metadata)[0].startswith('data:image/png;base64,')
    images.budget = 1
    assert images.load(metadata)[0] is None  # Repeated references also count toward HTML budget.
    assert _Images(tmp_path).load({**metadata, 'sha256': '0'*64})[0] is None
    assert _Images(tmp_path).load({**metadata, 'path': '../image.png'})[0] is None
    limited = _Images(tmp_path); limited.budget = 1
    assert '大小限制' in limited.load(metadata)[1]
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    (tmp_path / 'bad.svg').write_bytes(svg)
    assert _Images(tmp_path).load({'path': 'bad.svg', 'sha256': hashlib.sha256(svg).hexdigest()})[0] is None


def test_changed_resource_is_not_shown_as_original_preview(tmp_path, monkeypatch):
    failed_adapter(monkeypatch)
    config = load_config(); config['formula_fallback'] = 'isolate'
    task = tmp_path / 'task'
    core.parse_document(docx(tmp_path / 'a.docx', '<w:p>'+text('1．题干')+ole()+'</w:p>'+options()), 'choice', task, config)
    parsed = json.loads((task / 'task.json').read_text())
    (task / parsed['formulas'][0]['preview']['path']).write_bytes(b'changed')
    view = build_problem_preview(task)
    assert view['document_issues']
    assert not any(s['type'] == 'image' for q in view['questions'] for f in q['fields'] for s in f['segments'])


def test_standalone_html_escapes_document_text_and_only_embeds_trusted_math(tmp_path):
    view = {'source_name': '<img src=x onerror=alert(1)>', 'notice': 'not exported', 'questions': [{
        'number': '</h2><script>alert(1)</script>', 'kind_label': '单选',
        'fields': [{'label': '题干', 'problem_ids': [], 'segments': [
            {'type': 'text', 'text': '<script>alert(2)</script>'},
            {'type': 'latex', 'latex': r'x^2', 'text': '$$x^2$$'},
            {'type': 'image', 'src': 'javascript:alert(3)', 'alt': 'bad'},
        ]}], 'problems': []}]}
    output = render_preview_html(view)
    assert '<script>alert(1)' not in output and '<script>alert(2)' not in output
    assert 'javascript:alert(3)' not in output
    assert '&lt;script&gt;alert(2)' in output
    assert 'data-latex="x^2"' in output
    assert "default-src 'none'" in output and "form-action 'none'" in output
    assert 'src="http' not in output and 'href="file:' not in output
    if (Path(__file__).parents[1] / 'src/qa_parser/web/vendor/katex/katex.min.js').is_file():
        assert "script-src 'sha256-" in output and 'data:font/woff2;base64,' in output
        assert "QaMathPreview.render" in output and "QaFormulaFont" in output
        assert "data:font/otf;base64," in output


def test_duplicate_numbers_show_distinct_occurrences_and_source_text(tmp_path):
    body = paragraph('1．变形虫是淡水中的单细胞生物。') + options()
    body += paragraph('2．正常题。') + options()
    body += paragraph('1．另一组题目，观察洋葱细胞。') + options()
    task = tmp_path / 'task'
    core.parse_document(docx(tmp_path / '4.docx', body), 'auto', task)
    before = (task / 'task.json').read_bytes()
    view = build_problem_preview(task)
    questions = view['questions']
    assert len(questions) == 2
    assert [q['display_label'] for q in questions] == ['原题号 1 · 第1处', '原题号 1 · 第2处']
    assert all(q['source_occurrences'] == 2 for q in questions)
    assert questions[0]['source_excerpt'].startswith('变形虫')
    assert questions[1]['source_excerpt'].startswith('另一组题目')
    assert questions[0]['source_location_label'] != questions[1]['source_location_label']
    for q in questions:
        issue = next(p for p in q['problems'] if p['code'] == 'duplicate_number')
        assert '变形虫' in issue['reason'] and '另一组题目' in issue['reason']
        assert '边界识别有误' in issue['reason']
    output = render_preview_html(view)
    assert '原题号 1 · 第1处' in output and '原题号 1 · 第2处' in output
    assert '在 Word 中搜索题干开头' in output
    assert (task / 'task.json').read_bytes() == before


def test_source_occurrences_are_counted_before_problem_filtering():
    from qa_parser.problem_preview import _source_identities
    rows = [{'id': 'a', 'number': '1', 'stem': '甲', 'location': '/w:p[1]'},
            {'id': 'b', 'number': '1', 'stem': '乙', 'location': '/w:p[7]'},
            {'id': 'c', 'number': '3', 'stem': '丙', 'location': '/w:p[12]'}]
    identities, _ = _source_identities(rows)
    assert identities['b']['display_label'] == '原题号 1 · 第2处'
    assert identities['c']['display_label'] == '原题号 3'
    assert identities['c']['source_occurrences'] == 1
