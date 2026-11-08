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


def test_batch_package_mixed_names_partial_and_corrupt(tmp_path):
    first = docx(tmp_path / 'a/同名.docx', choice() + choice('2', 'AB'))
    second = docx(tmp_path / 'b/另一份.docx', ['1．填空____', '【答案】铁'])
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
            '批次结果说明.txt', '_说明/0001/结果说明.txt', '同名.xlsx', '同名/', '_说明/0001/未导出题目.html',
            '_说明/0002/结果说明.txt', '另一份.xlsx', '另一份/', '_说明/0003/结果说明.txt',
            '_说明/0001/题目预览.html', '_说明/0002/题目预览.html',
            '_display/NotoSansCJKsc-Regular.otf', '_display/OFL.txt'}
        assert '第 2 题' in z.read('_说明/0001/结果说明.txt').decode()
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
    task['questions'][0]['options']['A'] = '图示，参考0001.png'
    task['assets'] = [{'filename': '0001.png', 'path': '0001.png', 'sha256': hashlib.sha256(image).hexdigest()}]
    export_task(task, root, root / 'export')
    # Distractors must never be recursively copied into customer ZIP.
    (root / 'export/private.log').write_text('secret diagnostics')
    (root / 'export/source.docx').write_bytes(b'private source')
    (root / 'export/images/unused.png').write_bytes(b'unused')
    return image


def test_unpacked_excel_references_independent_images_and_only_manifest_payload(tmp_path):
    images = [image_export(tmp_path / 'a', 'red'), image_export(tmp_path / 'b', 'blue')]
    rows = [{'name': letter + ' 中文.docx', 'message': '转换成功', 'total': 1, 'passed': 1, 'quarantined': 0,
             'export': str(tmp_path / letter / 'export'), 'status': 'success'} for letter in ['a', 'b']]
    snapshot = {'message': '全部成功', 'total_files': 2, 'passed': 2, 'quarantined': 0, 'files': rows}
    result = publish_delivery(snapshot, tmp_path / 'final')
    with ZipFile(result['delivery']) as z:
        assert not any(name.endswith(('.json', '.log', '.docx')) for name in z.namelist())
        assert not any('unused' in name for name in z.namelist())
        z.extractall(tmp_path / 'unpacked')
    for letter, expected in zip(['a', 'b'], images):
        folder = tmp_path / 'unpacked' / (letter + ' 中文')
        with ZipFile(folder.with_suffix('.xlsx')) as z:
            xml = ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
            assert '参考0001.png' in ''.join(xml.itertext())
        assert (folder / '0001.png').read_bytes() == expected
        with Image.open(folder / '0001.png') as image:
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
        html = z.read('_说明/0001/未导出题目.html').decode()
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
    assert (tmp_path / 'final/_说明/0001/未导出题目.html').is_file()
    assert not list((tmp_path / 'final').rglob('*.xlsx'))
    assert not list((tmp_path / 'final').rglob('*.zip'))
    Path(row['problem_preview']).write_text('<script>untrusted</script>')
    with pytest.raises(ValueError, match='changed'):
        publish_delivery({'message': '失败', 'total_files': 1, 'passed': 0, 'quarantined': 1, 'files': [row]}, tmp_path / 'other')


def test_numeric_documents_keep_exact_names_and_separate_images(tmp_path):
    images = [image_export(tmp_path / 'a', 'red'), image_export(tmp_path / 'b', 'blue')]
    rows = [{'name': name + '.docx', 'message': '成功', 'total': 1, 'passed': 1,
             'quarantined': 0, 'export': str(tmp_path / folder / 'export')}
            for name, folder in [('01', 'a'), ('10', 'b')]]
    result = publish_delivery({'message': '成功', 'total_files': 2, 'passed': 2,
                               'quarantined': 0, 'files': rows}, tmp_path / 'final')
    with ZipFile(result['delivery']) as z:
        for name, expected in zip(['01', '10'], images):
            assert z.read(name + '/0001.png') == expected
            with ZipFile(io.BytesIO(z.read(name + '.xlsx'))) as workbook:
                assert '参考0001.png' in workbook.read('xl/worksheets/sheet1.xml').decode()
            assert not any(p.startswith(name + '/') and p.endswith(('.txt', '.html')) for p in z.namelist())
        assert not any('/images/' in p or p.endswith('questions.xlsx') for p in z.namelist())


def test_duplicate_numeric_names_fail_without_publishing(tmp_path):
    rows = [{'name': '1.docx'}, {'name': '1.docx'}]
    with pytest.raises(ValueError, match='文件名重复'):
        publish_delivery({'files': rows}, tmp_path / 'final')
    assert not (tmp_path / 'final').exists()


def test_numeric_batch_retains_preview_and_empty_image_folder(tmp_path):
    source = docx(tmp_path / '1.docx', choice())
    result = run_batch([source], 'auto', tmp_path / 'work')
    assert result['delivery_status'] == 'success'
    with ZipFile(result['delivery']) as z:
        assert '1.xlsx' in z.namelist() and '1/' in z.namelist()
        assert '_说明/0001/题目预览.html' in z.namelist()
        assert '根目录 1.xlsx' in z.read('_说明/0001/结果说明.txt').decode()


@pytest.mark.parametrize('names', [
    ['同名.docx', '同名.DOCX'], ['Test.docx', 'test.docx'], ['1.docx', '1.xlsx.docx'],
    ['_display.docx'], ['_说明.docx'], ['CON.docx'], ['a?.docx'], ['尾空格 .docx'],
    ['é.docx', 'e\u0301.docx'],
])
def test_delivery_rejects_cross_platform_name_collisions(names, tmp_path):
    from qa_parser.delivery import validate_delivery_names
    with pytest.raises(ValueError):
        validate_delivery_names(names)


def test_single_run_cli_uses_same_customer_layout(tmp_path, capsys):
    from qa_parser.cli import main
    source = docx(tmp_path / '物理 01.docx', choice())
    out = tmp_path / 'run'
    assert main(['run', str(source), '--kind', 'choice', '--out', str(out)]) == 0
    assert '最终结果' in capsys.readouterr().out
    with ZipFile(out / '最终结果/题库最终结果.zip') as archive:
        assert {'物理 01.xlsx', '物理 01/', '_说明/0001/题目预览.html'} <= set(archive.namelist())
        html = archive.read('_说明/0001/题目预览.html').decode()
        assert 'url(../../_display/NotoSansCJKsc-Regular.otf)' in html
        assert not any(name.endswith('/questions.xlsx') or '/images/' in name for name in archive.namelist())
