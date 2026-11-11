"""Special handoff never relaxes acceptance for ordinary question types."""
import hashlib
import json
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from PIL import Image
import pytest

from qa_parser.core import run_document
from qa_parser.delivery import publish_single_delivery
from qa_parser.export_preview import _workbook_rows
from test_core import p, write_docx


def test_no_analysis_is_valid_for_choice_and_plain_blank(tmp_path):
    body = p('1．名称____。') + p('【答案】甲')
    body += p('2．选择？') + p('A．甲 B．乙 C．丙 D．丁') + p('【答案】B')
    source = write_docx(tmp_path / 'plain.docx', body)
    result = run_document(source, 'auto', tmp_path / 'out')
    assert result['passed'] == 2 and result['quarantined'] == 0
    assert not result.get('special_questions')
    task = json.loads((tmp_path / 'out/task/task.json').read_text())
    assert all(q['analysis_status'] == 'absent' and q['analysis'] == '解析无' for q in task['questions'])
    assert task['questions'][0]['knowledge_point'] == '名称甲。'
    rows = dict(_workbook_rows(tmp_path / 'out/export/questions.xlsx'))
    assert dict(zip(rows[1], rows[2]))['解析'] == '解析无'


def test_special_package_preserves_original_answers_and_does_not_change_normal_rows(tmp_path):
    image = BytesIO()
    Image.new('RGB', (8, 8), 'blue').save(image, format='PNG')
    body = p('1．名称____。') + p('【答案】甲')
    body += p('2．解释图中的原因。')
    body += '<w:p><w:r><w:drawing><a:blip r:embed="rId1"/></w:drawing></w:r></w:p>'
    body += p('【答案】原因说明。')
    body += p('3．前项____，后项____。') + p('【答案】甲，乙')
    source = write_docx(tmp_path / 'mixed.docx', body, {'figure.png': image.getvalue()})
    out = tmp_path / 'out'
    result = run_document(source, 'auto', out)
    assert result['passed'] == 2 and result['quarantined'] == 1
    meta = result['special_questions']
    assert meta['total'] == 1 and meta['status'] == 'pending_postprocessing'
    archive_path = out / meta['path']
    assert hashlib.sha256(archive_path.read_bytes()).hexdigest() == meta['sha256']
    with ZipFile(archive_path) as archive:
        queue = json.loads(archive.read('待后处理.json'))
        assert [q['number'] for q in queue['questions']] == ['3']
        assert queue['questions'][0]['raw_fields']['answer'] == '甲，乙'
        assert queue['questions'][0]['question']['stem'].count('____') == 2
        assert queue['questions'][0]['question']['knowledge_point'] == ''
        assert all(q['question']['analysis_status'] == 'absent' for q in queue['questions'])
        assert archive.read(queue['source']['snapshot']['path']) == source.read_bytes()
        assert not queue['assets']
    manifest = json.loads((out / 'export/report.json').read_text())
    assert [row['number'] for row in manifest['rows']] == ['1', '2']
    delivered = publish_single_delivery(result, out)
    with ZipFile(delivered['delivery']) as archive:
        assert archive.read('_说明/0001/特殊题型待后处理.zip') == archive_path.read_bytes()


def test_policy_skips_are_not_relabelled_as_special_success(tmp_path):
    body = p('1．填写下表。') + '<w:tbl><w:tr><w:tc>' + p('名称') + '</w:tc><w:tc>' + p('____') + '</w:tc></w:tr></w:tbl>' + p('【答案】甲')
    source = write_docx(tmp_path / 'table.docx', body)
    result = run_document(source, 'auto', tmp_path / 'out')
    assert result['counts']['policy_skipped'] == 1 and result['passed'] == 0
    assert not result.get('special_questions')


def test_complete_special_response_exports_actual_workbook(tmp_path):
    source = write_docx(tmp_path / 'open.docx', p('1．解释原因。') + p('【答案】原因说明。'))
    out = tmp_path / 'out'
    result = run_document(source, 'auto', out)
    assert result['passed'] == 1 and result['quarantined'] == 0
    assert not result.get('special_questions')
    rows = dict(_workbook_rows(out / 'export/questions.xlsx'))
    row = dict(zip(rows[1], rows[2]))
    assert row['题目'] == '解释原因。' and row['答案'] == '原因说明。'
    assert row['解析'] == '解析无' and row['类型'] == '简答'
    assert publish_single_delivery(result, out)['delivery']


def test_special_packaging_error_keeps_normal_export(tmp_path, monkeypatch):
    def fail(*args):
        raise OSError('simulated special package failure')
    monkeypatch.setattr('qa_parser.special_questions.write_special_queue', fail)
    source = write_docx(tmp_path / 'mixed.docx', p('1．名称____。') + p('【答案】甲') + p('2．前项____后项____。') + p('【答案】说明。'))
    result = run_document(source, 'auto', tmp_path / 'out')
    assert result['passed'] == 1 and result['quarantined'] == 1
    assert 'special_questions_error' in result
    assert (tmp_path / 'out/export/questions.xlsx').is_file()

@pytest.mark.parametrize('answer,passed', [
    ('(1)甲  乙\n(2)原因说明。', True),
    ('(1)甲\n(2)原因说明。', False),
    ('(1)甲  乙\n(3)原因说明。', False),
    ('(1)甲  乙\n(2)', False),
    ('(1)甲  乙\n(1)原因说明。', False),
])
def test_mixed_response_requires_every_blank_and_exact_subpart_labels(tmp_path, answer, passed):
    body = p('1．实验。') + p('(1)前项____，后项____。') + p('(2)解释原因。') + p('【答案】' + answer)
    result = run_document(write_docx(tmp_path / 'mixed.docx', body), 'auto', tmp_path / 'out')
    assert result['passed'] == int(passed)
    if passed:
        task = json.loads((tmp_path / 'out/task/task.json').read_text())
        q = task['questions'][0]
        assert q['kind'] == 'response' and q['answer'] == answer
        assert q['stem'].count('____') == 2 and q['knowledge_point'] == ''


def test_explicit_unsupported_section_is_not_promoted(tmp_path):
    body = p('一、判断题') + p('1．地球是平的。') + p('【答案】错')
    result = run_document(write_docx(tmp_path / 'judge.docx', body), 'auto', tmp_path / 'out')
    assert result['passed'] == 0 and result['quarantined'] == 1
