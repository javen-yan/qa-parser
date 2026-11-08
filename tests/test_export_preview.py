"""The readable export follows literal workbook cells and verified resources."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path

from lxml import html
from PIL import Image
import pytest

from qa_parser.export_preview import build_export_preview, render_export_preview
from qa_parser.exporter import export_task
from test_exporter import sample_task


def exported(tmp_path, *, image=False):
    task = sample_task()
    task['source']['name'] = '显示 <script>bad()</script>.docx'
    task['questions'][0]['number'] = '7'
    task['questions'][0]['stem'] = '比较 $$x_1$$ 与图；<script>alert(1)</script>'
    task['questions'][0]['options']['A'] = '参考0001.png' if image else '甲'
    failed = deepcopy(task['questions'][0])
    failed.update(id='q2', number='9', stem='不得出现在题目正文', issues=[{'code': 'formula_review'}])
    task['questions'].append(failed)
    if image:
        stream = io.BytesIO()
        Image.new('RGB', (12, 8), 'green').save(stream, format='PNG')
        data = stream.getvalue()
        (tmp_path / '0001.png').write_bytes(data)
        task['questions'][0]['asset_filenames'] = ['0001.png']
        task['assets'] = [{'filename': '0001.png', 'path': '0001.png', 'sha256': hashlib.sha256(data).hexdigest()}]
    report = export_task(task, tmp_path, tmp_path / 'export', allow_partial=True)
    return tmp_path / 'export', report


def test_actual_export_fields_source_numbers_and_omissions(tmp_path):
    folder, _ = exported(tmp_path)
    preview = build_export_preview(folder)
    assert preview['counts'] == {'questions': 1, 'formulas': 2, 'image_occurrences': 0,
                                 'image_placeholders': 0, 'omitted_questions': 1}
    q = preview['questions'][0]
    assert q['number'] == '7' and q['excel_row'] == 2
    assert [s['latex'] for f in q['fields'] for s in f['segments'] if s['type'] == 'latex'] == ['x_1', 'x^2']
    assert preview['omitted_numbers'] == ['9']
    output, summary = render_export_preview(folder)
    document = html.fromstring(output)
    assert len(document.xpath('//article')) == 1
    assert '不得出现在题目正文' not in output
    assert '<script>alert(1)</script>' not in output
    assert '比较 $$x_1$$' in document.text_content()  # Exact original columns remain available.
    assert not document.xpath('//iframe|//object|//embed|//form|//link')
    assert all(not any(k.startswith('on') for k in n.attrib) for n in document.iter())
    for script in document.xpath('//script'):
        import base64
        digest = base64.b64encode(hashlib.sha256(script.text.encode()).digest()).decode()
        assert "'sha256-" + digest + "'" in document.xpath('//meta[@http-equiv="Content-Security-Policy"]/@content')[0]
    assert summary['formulas'] == 2
    assert str(tmp_path) not in output


def test_relative_image_matches_manifest_and_tampering_fails(tmp_path):
    folder, _ = exported(tmp_path, image=True)
    output, summary = render_export_preview(folder)
    document = html.fromstring(output)
    assert document.xpath('//img/@src') == ['images/0001.png']
    assert summary['image_occurrences'] == 1 and summary['image_placeholders'] == 0
    assert 'data:image/png' not in output  # No repeated image base64 payloads.
    (folder / 'images/0001.png').write_bytes(b'changed')
    with pytest.raises(ValueError, match='integrity mismatch'):
        build_export_preview(folder)


def test_mismatched_source_row_map_and_unsafe_image_names_fail(tmp_path):
    folder, manifest = exported(tmp_path, image=True)
    original = deepcopy(manifest)
    manifest['rows'][0]['excel_row'] = 42
    (folder / 'report.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='source mapping'):
        build_export_preview(folder)
    original['images'][0]['filename'] = '../outside.png'
    (folder / 'report.json').write_text(json.dumps(original))
    with pytest.raises(ValueError, match='Unsafe'):
        build_export_preview(folder)


def test_preview_obeys_actual_mapped_columns_and_configured_delimiter(tmp_path):
    task = sample_task()
    task['config'] = {'column_fields': {'题目': 'answer'}}
    task['questions'][0]['answer'] = r'条件 %%x_2%%'
    task['questions'][0]['analysis'] = ''
    export_task(task, tmp_path, tmp_path / 'export')
    preview = build_export_preview(tmp_path / 'export', formula_delimiter='%%')
    assert preview['counts']['formulas'] == 2  # Actual mapped stem + answer.
    preview = build_export_preview(tmp_path / 'export', formula_delimiter='$$')
    stem = next(f for f in preview['questions'][0]['fields'] if f['key'] == '题目')
    assert stem['segments'] == [{'type': 'text', 'text': r'条件 %%x_2%%'}]


def test_unsupported_real_image_is_explicit_placeholder(tmp_path):
    task = sample_task()
    stream = io.BytesIO()
    Image.new('RGB', (5, 5), 'red').save(stream, format='TIFF')
    data = stream.getvalue()
    (tmp_path / '0001.tiff').write_bytes(data)
    task['questions'][0]['stem'] = '图示参考0001.tiff；其余文字保留'
    task['questions'][0]['asset_filenames'] = ['0001.tiff']
    task['assets'] = [{'filename': '0001.tiff', 'path': '0001.tiff', 'sha256': hashlib.sha256(data).hexdigest()}]
    export_task(task, tmp_path, tmp_path / 'export')
    preview = build_export_preview(tmp_path / 'export')
    assert preview['counts']['image_placeholders'] == 1
    assert any(s['type'] == 'placeholder' for f in preview['questions'][0]['fields'] for s in f['segments'])


def test_reference_choice_reads_real_export_and_all_hundred_rows(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'reference/MathType公式化学2.docx'
    if not source.exists():
        pytest.skip('Reference fixtures unavailable')
    from qa_parser.core import run_document
    run_document(source, 'choice', tmp_path / 'job')
    preview = build_export_preview(tmp_path / 'job/export')
    assert [q['number'] for q in preview['questions']] == [str(i) for i in range(1, 101)]
    first = preview['questions'][0]
    by_key = {f['key']: f['segments'] for f in first['fields']}
    assert next(s['latex'] for s in by_key['optionA'] if s['type'] == 'latex') == r'\xrightarrow{\text{点燃}}'
    assert next(s['latex'] for s in by_key['optionD'] if s['type'] == 'latex') == r'\xrightarrow{\Delta}'
    assert preview['counts']['image_placeholders'] == 0


@pytest.mark.parametrize('reference', ['参考{filename}', '请参考 {filename}', '[图片:{filename}]'])
def test_image_reference_contract_all_fields_and_custom_preview(tmp_path, reference):
    from qa_parser.config import DEFAULT_CONFIG, load_config
    from test_exporter import cells
    assert DEFAULT_CONFIG['image_reference'] == '参考{filename}'
    configured = load_config(Path(__file__).resolve().parents[1] / 'config/default.json')
    assert configured['image_reference'] == DEFAULT_CONFIG['image_reference']
    task = sample_task()
    task['config']['image_reference'] = reference
    stream = io.BytesIO()
    Image.new('RGB', (12, 8), 'green').save(stream, format='PNG')
    data = stream.getvalue()
    (tmp_path / '0001.png').write_bytes(data)
    text = reference.format(filename='0001.png')
    question = task['questions'][0]
    for field in ('stem', 'answer', 'analysis'):
        question[field] = '前' + text + '后'
    question['options']['A'] = text
    question['asset_filenames'] = ['0001.png']
    task['assets'] = [{'filename': '0001.png', 'path': '0001.png', 'sha256': hashlib.sha256(data).hexdigest()}]
    folder = tmp_path / 'export'
    export_task(task, tmp_path, folder)
    _, values = cells(folder / 'questions.xlsx')
    assert all(values[column + '2'] == '前' + text + '后' for column in ('D', 'F', 'G'))
    assert values['I2'] == text and 'A．' + text in values['E2']
    assert [p.name for p in (folder / 'images').iterdir()] == ['0001.png']
    options = {} if reference == '参考{filename}' else {'image_reference': reference}
    preview = build_export_preview(folder, **options)
    images = [s for f in preview['questions'][0]['fields'] for s in f['segments'] if s['type'] == 'image']
    assert len(images) == 4
    output, summary = render_export_preview(folder, **options)
    assert html.fromstring(output).xpath('//img/@src') == ['images/0001.png'] * 4
    assert summary['image_occurrences'] == 4
