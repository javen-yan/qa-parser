"""The portable ZIP is the customer artifact, not a diagnostic directory."""
import hashlib
import io
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from PIL import Image
import pytest

from qa_parser.batch import run_batch
from qa_parser.delivery import publish_delivery
from qa_parser.exporter import export_task
from test_batch import choice, docx
from test_exporter import sample_task


def test_batch_package_mixed_same_name_partial_and_corrupt(tmp_path):
    first = docx(tmp_path / 'a/同名.docx', choice() + choice('2', 'AB'))
    second = docx(tmp_path / 'b/同名.docx', ['1．填空____', '【答案】铁'])
    broken = tmp_path / '损坏.docx'
    broken.write_bytes(b'not word')
    work = tmp_path / '诊断'
    final = tmp_path / '客户 最终结果'
    events = []
    result = run_batch([first, second, broken], 'auto', work, events.append, delivery_directory=final)
    assert result['status'] == 'partial_failure'
    assert result['delivery_status'] == 'success'
    assert result['passed'] == 2 and result['quarantined'] == 1
    assert set(p.name for p in final.iterdir()) == {'题库最终结果.zip', '批次结果说明.txt'}
    report = Path(result['delivery_report']).read_text()
    assert '第 2 题' in report and '未导出 1 题' in report
    assert '无法读取或识别，不能确认数量' in report
    assert str(tmp_path) not in report
    with ZipFile(result['delivery']) as z:
        assert z.testzip() is None
        assert set(z.namelist()) == {
            '批次结果说明.txt', '0001-同名/结果说明.txt', '0001-同名/questions.xlsx', '0001-同名/未导出题目.html',
            '0002-同名/结果说明.txt', '0002-同名/questions.xlsx', '0003-损坏/结果说明.txt',
            '0001-同名/题目预览.html', '0002-同名/题目预览.html',
            '_display/NotoSansCJKsc-Regular.otf', '_display/OFL.txt'}
        assert '第 2 题' in z.read('0001-同名/结果说明.txt').decode()
        z.extractall(tmp_path / '解压')
    for xlsx in (tmp_path / '解压').rglob('*.xlsx'):
        with ZipFile(xlsx) as z:
            assert z.testzip() is None
            assert z.read('xl/worksheets/sheet1.xml')
    # Completion notifications cannot precede the usable final package.
    assert events[-2]['status'] == 'running'
    assert events[-1]['delivery'] == result['delivery']
    assert json.loads((work / 'batch-summary.json').read_text()) == result
    assert (work / '.inputs').exists()  # diagnostics preserved separately


def image_export(root, color):
    root.mkdir()
    data = io.BytesIO()
    Image.new('RGB', (2, 2), color).save(data, 'PNG')
    image = data.getvalue()
    (root / '0001.png').write_bytes(image)
    task = sample_task()
    task['questions'][0]['options']['A'] = '图示，请参考 0001.png'
    task['assets'] = [{'filename': '0001.png', 'path': '0001.png', 'sha256': hashlib.sha256(image).hexdigest()}]
    export_task(task, root, root / 'export')
    # Distractors must never be recursively copied into customer ZIP.
    (root / 'export/private.log').write_text('secret diagnostics')
    (root / 'export/source.docx').write_bytes(b'private source')
    (root / 'export/images/unused.png').write_bytes(b'unused')
    return image


def test_unpacked_excel_references_independent_images_and_only_manifest_payload(tmp_path):
    images = [image_export(tmp_path / 'a', 'red'), image_export(tmp_path / 'b', 'blue')]
    rows = [{'name': '同名.docx', 'message': '转换成功', 'total': 1, 'passed': 1, 'quarantined': 0,
             'export': str(tmp_path / letter / 'export'), 'status': 'success'} for letter in ['a', 'b']]
    snapshot = {'message': '全部成功', 'total_files': 2, 'passed': 2, 'quarantined': 0, 'files': rows}
    result = publish_delivery(snapshot, tmp_path / 'final')
    with ZipFile(result['delivery']) as z:
        assert not any(name.endswith(('.json', '.log', '.docx')) for name in z.namelist())
        assert not any('unused' in name for name in z.namelist())
        z.extractall(tmp_path / 'unpacked')
    for index, expected in enumerate(images, 1):
        folder = tmp_path / 'unpacked' / f'{index:04d}-同名'
        with ZipFile(folder / 'questions.xlsx') as z:
            xml = ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
            assert '请参考 0001.png' in ''.join(xml.itertext())
        assert (folder / 'images/0001.png').read_bytes() == expected
        with Image.open(folder / 'images/0001.png') as image:
            image.verify()


def test_entire_failure_only_explanatory_report_and_source_numbers(tmp_path):
    source = docx(tmp_path / 'input.docx', ['12．填空____', '【答案】铁'])
    result = run_batch([source], 'choice', tmp_path / 'work', delivery_directory=tmp_path / 'final')
    assert result['status'] == 'failure' and result['delivery'] is None
    assert list((tmp_path / 'final').iterdir()) == [Path(result['delivery_report'])]
    report = Path(result['delivery_report']).read_text()
    assert '第 12 题' in report and '不一致' in report
    assert not list((tmp_path / 'final').glob('*.zip'))


def test_existing_delivery_is_not_replaced(tmp_path):
    source = docx(tmp_path / 'input.docx', choice())
    final = tmp_path / 'final'
    final.mkdir()
    marker = final / 'keep'
    marker.write_text('original')
    result = run_batch([source], 'auto', tmp_path / 'work', delivery_directory=final)
    assert result['status'] == result['delivery_status'] == 'failure'
    assert result['delivery'] is None
    assert result['passed'] == 1  # parsing result retained, packaging failure explicit
    assert marker.read_text() == 'original'
    assert list(final.iterdir()) == [marker]


def test_tampered_export_image_prevents_final_package(tmp_path):
    image_export(tmp_path / 'input', 'red')
    (tmp_path / 'input/export/images/0001.png').write_bytes(b'changed')
    row = {'name': 'x.docx', 'message': 'success', 'total': 1, 'passed': 1, 'quarantined': 0,
           'export': str(tmp_path / 'input/export')}
    snapshot = {'message': 'success', 'total_files': 1, 'passed': 1, 'quarantined': 0, 'files': [row]}
    with pytest.raises(ValueError, match='changed'):
        publish_delivery(snapshot, tmp_path / 'final')
    assert not (tmp_path / 'final').exists()
    assert not list(tmp_path.glob('.qa-delivery-*'))


def test_failed_questions_preview_is_offline_and_only_failed_questions(tmp_path):
    source = docx(tmp_path / '题.docx', choice() + choice('2', 'AB'))
    result = run_batch([source], 'auto', tmp_path / 'work')
    with ZipFile(result['delivery']) as z:
        html = z.read('0001-题/未导出题目.html').decode()
        assert '第 2 题' in html and '第 1 题' not in html
        assert '未导出' in html and 'Content-Security-Policy' in html
        assert '<script src=' not in html and '<link' not in html
        assert not any(name.endswith(('.docx', '.bin', '.xml', '.json')) for name in z.namelist())
    report = Path(result['delivery_report']).read_text()
    assert '答案' in report and '未导出题目.html' in report


def test_all_questions_failed_still_has_explanatory_html_without_success_zip(tmp_path):
    # Direct run creates a readable task even when every row is unsuitable.
    from qa_parser.core import run_document
    source = docx(tmp_path / 'input.docx', choice('1', 'AB'))
    folder = tmp_path / 'work'
    summary = run_document(source, 'choice', folder)
    row = {'name': source.name, 'message': summary['user_result']['message'], 'total': 1,
           'passed': 0, 'quarantined': 1, 'export': None,
           'problem_preview': str(folder / summary['problem_preview']['path']),
           'problem_preview_sha256': summary['problem_preview']['sha256']}
    result = publish_delivery({'message': '失败', 'total_files': 1, 'passed': 0, 'quarantined': 1, 'files': [row]}, tmp_path / 'final')
    assert result['delivery'] is None
    assert (tmp_path / 'final/0001-input/未导出题目.html').is_file()
    assert not list((tmp_path / 'final').rglob('*.xlsx'))
    assert not list((tmp_path / 'final').rglob('*.zip'))
    Path(row['problem_preview']).write_text('<script>untrusted</script>')
    with pytest.raises(ValueError, match='changed'):
        publish_delivery({'message': '失败', 'total_files': 1, 'passed': 0, 'quarantined': 1, 'files': [row]}, tmp_path / 'other')
