import io
import json
from pathlib import Path
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

from qa_parser.local_ui import Application, LocalServer, UIError


def test_same_named_uploads_are_distinct_and_remove_only_one(tmp_path):
    app = Application(tmp_path / 'state', tmp_path / 'out')
    app.add_file('题目.docx', io.BytesIO(b'first'), 5)
    app.add_file('题目.docx', io.BytesIO(b'second'), 6)
    assert len(app.files) == 2
    assert app.files[0]['path'] != app.files[1]['path']
    app.remove(app.files[0]['id'])
    assert app.files[0]['path'].read_bytes() == b'second'
    assert 'path' not in app.state()['files'][0]


def test_incomplete_upload_cleans_partial_file(tmp_path):
    app = Application(tmp_path / 'state', tmp_path / 'out')
    with pytest.raises(UIError, match='中断'):
        app.add_file('a.docx', io.BytesIO(b'abc'), 9)
    assert not list(app.uploads.iterdir())
    assert not app.files


def test_upload_constraints_and_busy_guard(tmp_path):
    app = Application(tmp_path / 'state', tmp_path / 'out')
    with pytest.raises(UIError):
        app.add_file('a.xlsx', io.BytesIO(b'abc'), 3)
    app.busy = True
    for operation in [lambda: app.add_file('a.docx', io.BytesIO(b'abc'), 3),
                      lambda: app.start('auto'), app.reset, lambda: app.choose_output(None)]:
        with pytest.raises(UIError) as error:
            operation()
        assert error.value.status == 409


def test_worker_responsive_and_prevents_double_start(tmp_path, monkeypatch):
    import qa_parser.batch
    entered, release = threading.Event(), threading.Event()
    def fake_batch(inputs, mode, out, progress, *, delivery_directory):
        assert out.is_relative_to(tmp_path / "state")
        assert delivery_directory.is_relative_to(tmp_path / "out")
        entered.set()
        assert release.wait(3)
        return {'status': 'success', 'files': [], 'total_files': 1, 'completed': 1}
    monkeypatch.setattr(qa_parser.batch, 'run_batch', fake_batch)
    app = Application(tmp_path / 'state', tmp_path / 'out')
    app.add_file('a.docx', io.BytesIO(b'abc'), 3)
    try:
        assert app.start('auto')['busy']
        assert entered.wait(1)
        assert app.state()['busy']
        with pytest.raises(UIError):
            app.start('auto')
        with pytest.raises(UIError):
            app.reset()
    finally:
        release.set()
        app.worker.join(3)
    assert not app.state()['busy']
    with pytest.raises(UIError):
        app.start('auto')
    app.reset()
    assert app.state()['batch'] is None


def test_server_auth_origin_static_allowlist_and_json(tmp_path):
    app = Application(tmp_path / 'state', tmp_path / 'out')
    server = LocalServer(app)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    def request(path, headers=None, data=None):
        return urlopen(Request(server.origin + path, data=data, headers=headers or {}), timeout=3)
    try:
        with request('/') as response:
            content = response.read()
            assert app.token.encode() in content
            assert b'__QA_TOKEN__' not in content
            assert "frame-ancestors 'none'" in response.headers['Content-Security-Policy']
        for path, headers in [('/api/state', {}), ('/api/state', {'X-QA-Token': app.token, 'Origin': 'https://evil.example'}),
                              ('/', {'Host': 'evil.example'}), ('/../../README.md', {})]:
            with pytest.raises(HTTPError) as error:
                request(path, headers)
            assert error.value.code in {403, 404}
        with request('/api/state', {'X-QA-Token': app.token}) as response:
            assert json.load(response)['busy'] is False
        with pytest.raises(HTTPError) as error:
            request('/api/start', {'X-QA-Token': app.token}, b'[]')
        assert error.value.code == 400
    finally:
        server.shutdown()
        thread.join(3)
        server.server_close()


def test_output_open_only_server_known_paths(tmp_path, monkeypatch):
    app = Application(tmp_path / 'state', tmp_path / 'out')
    opened = []
    monkeypatch.setattr('qa_parser.local_ui.open_local_path', lambda path: opened.append(path))
    app.batch = {'output': str(tmp_path), 'files': [
        {'status': 'partial_failure', 'export': str(tmp_path / 'export'), 'report': str(tmp_path / 'result.txt')}]}
    app.open_result('report', 0)
    assert opened == [tmp_path / 'result.txt']
    with pytest.raises(UIError):
        app.open_result('/etc/passwd', 0)
    with pytest.raises(UIError):
        app.open_result('report', -1)


def test_report_is_plain_text_and_only_known_batch_entries(tmp_path):
    app = Application(tmp_path / 'state', tmp_path / 'out')
    report = tmp_path / 'result.txt'
    report.write_text('部分失败：第2题未导出。', encoding='utf-8')
    app.batch = {'files': [{'name': '题.docx', 'report': str(report)}]}
    assert app.read_report(0) == {'name': '题.docx', 'text': '部分失败：第2题未导出。'}
    with pytest.raises(UIError):
        app.read_report(-1)
    with pytest.raises(UIError):
        app.read_report(1)


def test_choose_output_and_folder_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    app = Application(tmp_path / 'state', tmp_path / 'out')
    selected = tmp_path / '中文 保存位置'
    selected.mkdir()
    (tmp_path / '.hidden').mkdir()
    assert app.folders()['path'] == str(tmp_path)
    assert '.hidden' not in [f['name'] for f in app.folders()['folders']]
    assert app.choose_output(str(selected))['output_directory'] == str(selected)
    with pytest.raises(UIError):
        app.choose_output(str(tmp_path.parent))
    with pytest.raises(UIError):
        app.folders(str(tmp_path / 'missing'))
    app.batch = {'status': 'success'}
    with pytest.raises(UIError):
        app.choose_output(str(tmp_path))


def test_problem_preview_uses_only_batch_task_and_verified_source(tmp_path, monkeypatch):
    from qa_parser.batch import run_batch
    from test_batch import docx, choice
    source = docx(tmp_path / '源 题.docx', choice() + choice('2', 'AB'))
    app = Application(tmp_path / 'state', tmp_path / 'final')
    app.batch = run_batch([source], 'auto', tmp_path / 'work')
    before = (tmp_path / 'work/0001-源 题/task/task.json').read_bytes()
    preview = app.read_problems(0)
    assert preview['source_name'] == '源 题.docx'
    assert preview['source_available'] is True
    assert [q['number'] for q in preview['questions']] == ['2']
    assert preview['questions'][0]['problems']
    assert before == (tmp_path / 'work/0001-源 题/task/task.json').read_bytes()
    opened = []
    monkeypatch.setattr('qa_parser.local_ui.open_local_path', lambda path: opened.append(path))
    app.open_result('source', 0)
    assert opened == [Path(app.batch['files'][0]['source_snapshot'])]
    opened[0].write_bytes(b'changed')
    assert app.read_problems(0)['source_available'] is False
    with pytest.raises(UIError, match='变化'):
        app.open_result('source', 0)
    for bad in [-1, 1, True, '/etc/passwd']:
        with pytest.raises(UIError):
            app.read_problems(bad)


def test_problem_preview_unreadable_and_no_problem_files(tmp_path):
    from qa_parser.batch import run_batch
    from test_batch import docx, choice
    source = docx(tmp_path / '正常.docx', choice())
    broken = tmp_path / '损坏.docx'
    broken.write_bytes(b'not a ZIP')
    app = Application(tmp_path / 'state', tmp_path / 'final')
    app.batch = run_batch([source, broken], 'auto', tmp_path / 'work')
    good = app.read_problems(0)
    assert good['questions'] == [] and good['empty_message']
    bad = app.read_problems(1)
    assert bad['questions'] == [] and bad['document_issues']
    assert '无法读取' in bad['empty_message']
    assert bad['source_available'] is True


def test_export_preview_uses_actual_rows_and_rejects_invalid_index(tmp_path):
    from qa_parser.batch import run_batch
    from test_delivery import docx, choice
    source = docx(tmp_path / 'read.docx', choice() + choice('2', 'AB'))
    app = Application(tmp_path / 'state', tmp_path / 'out')
    app.batch = run_batch([source], 'auto', tmp_path / 'batch')
    preview = app.read_export_preview(0)
    assert preview['exported'] is True
    assert [q['number'] for q in preview['questions']] == ['1']
    assert preview['questions'][0]['excel_row'] == 2
    assert preview['omitted_numbers'] == ['2']
    for index in (-1, 1, True, '0'):
        with pytest.raises(UIError):
            app.read_export_preview(index)
