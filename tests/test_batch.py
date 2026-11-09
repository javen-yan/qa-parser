import json
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZipFile

import pytest

from qa_parser.batch import run_batch


def docx(path, paragraphs):
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, 'w') as archive:
        body = ''.join('<w:p><w:r><w:t>' + escape(p) + '</w:t></w:r></w:p>' for p in paragraphs)
        archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body + '</w:body></w:document>')
    return path


def choice(number='1', answer='A'):
    return [f'{number}．选择正确项', 'A．甲 B．乙 C．丙 D．丁', f'【答案】{answer}']


def test_auto_named_files_and_corrupt_file_are_isolated(tmp_path):
    first = docx(tmp_path / 'one/相同文件.docx', choice())
    second = docx(tmp_path / 'two/另一文件.docx', ['1．填空____', '【答案】乙'])
    broken = tmp_path / 'broken.docx'
    broken.write_bytes(b'broken')
    events = []
    result = run_batch([first, broken, second], 'auto', tmp_path / 'outputs', events.append)
    assert result['status'] == 'partial_failure'
    assert result['completed'] == result['total_files'] == 3
    assert [f['status'] for f in result['files']] == ['success', 'failure', 'success']
    assert [f['kind'] for f in result['files']] == ['choice', None, 'blank']
    assert result['passed'] == 2
    assert len({f['output'] for f in result['files']}) == 3
    for row in result['files']:
        assert Path(row['report']).is_file()
        if row['status'] == 'success':
            assert (Path(row['export']) / 'questions.xlsx').is_file()
            assert (Path(row['output']) / 'classification.json').is_file()
    assert events[0]['files'][0]['status'] == 'queued'
    assert events[-1]['status'] == 'partial_failure'
    assert json.loads((tmp_path / 'outputs/batch-summary.json').read_text()) == result


@pytest.mark.parametrize('mode,paragraphs', [('choice', ['1．填空____', '【答案】铁']), ('blank', choice())])
def test_manual_mismatch_blocks_output(tmp_path, mode, paragraphs):
    source = docx(tmp_path / 'source.docx', paragraphs)
    result = run_batch([source], mode, tmp_path / 'out')
    row = result['files'][0]
    assert result['status'] == row['status'] == 'failure'
    assert row['passed'] == 0 and row['quarantined'] == 1
    assert row['export'] is None
    assert '不一致' in row['message']
    assert not list((tmp_path / 'out').rglob('*.xlsx'))


@pytest.mark.parametrize('mode,paragraphs', [('choice', choice()), ('blank', ['1．填空____', '【答案】铁'])])
def test_manual_modes_use_matching_core(tmp_path, mode, paragraphs):
    result = run_batch([docx(tmp_path / 'source.docx', paragraphs)], mode, tmp_path / 'out')
    assert result['status'] == 'success' and result['passed'] == 1


def test_partial_file_is_not_success(tmp_path):
    source = docx(tmp_path / 'source.docx', choice() + choice('2', 'AB'))
    result = run_batch([source], 'auto', tmp_path / 'out')
    assert result['status'] == 'partial_failure'
    row = result['files'][0]
    assert row['passed'] == row['quarantined'] == 1
    assert '不完整' in row['message']


def test_mixed_exports_supported_rows_and_unknown_remains_reported(tmp_path):
    mixed = docx(tmp_path / 'mixed.docx', choice() + ['2．填空____', '【答案】铁'])
    unknown = docx(tmp_path / 'unknown.docx', ['1．解释这个现象。', '【答案】答案'])
    for mode in ['auto', 'choice', 'blank']:
        result = run_batch([mixed, unknown], mode, tmp_path / mode)
        assert result['status'] == 'partial_failure'
        assert result['files'][0]['passed'] == (2 if mode == 'auto' else 1)
        assert result['files'][0]['export']
        assert result['files'][1]['export'] is None
        report = json.loads((Path(result['files'][0]['output']) / 'export/report.json').read_text())
        assert len(report['sheets']) == (2 if mode == 'auto' else 1)
        assert report['import_contract'] == 'unverified'


def test_callback_error_does_not_fail_conversion(tmp_path):
    source = docx(tmp_path / 'source.docx', choice())
    def disconnected(snapshot):
        raise RuntimeError('browser closed')
    assert run_batch([source], 'auto', tmp_path / 'out', disconnected)['status'] == 'success'


def test_existing_output_rejected_without_overwrite(tmp_path):
    root = tmp_path / 'out'
    root.mkdir()
    marker = root / 'marker'
    marker.write_text('unchanged')
    with pytest.raises(FileExistsError):
        run_batch([tmp_path / 'missing.docx'], 'auto', root)
    assert marker.read_text() == 'unchanged'


def test_missing_input_failure_does_not_stop_later_file(tmp_path):
    source = docx(tmp_path / 'source.docx', choice())
    result = run_batch([tmp_path / 'missing.docx', source], 'auto', tmp_path / 'out')
    assert [r['status'] for r in result['files']] == ['failure', 'success']


def test_bad_batch_arguments_do_not_create_output(tmp_path):
    out = tmp_path / 'out'
    with pytest.raises(ValueError):
        run_batch([], 'auto', out)
    with pytest.raises(ValueError):
        run_batch([tmp_path / 'missing.docx'], 'unknown', out)
    assert not out.exists()


def test_duplicate_numeric_names_rejected_before_conversion(tmp_path):
    with pytest.raises(ValueError, match='文件名重复'):
        run_batch([tmp_path / 'a/1.docx', tmp_path / 'b/1.DOCX'], 'auto', tmp_path / 'work')
    assert not (tmp_path / 'work').exists()
