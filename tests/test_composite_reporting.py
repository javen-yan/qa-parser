import json
from zipfile import ZipFile

from qa_parser.core import run_document
from qa_parser.delivery import publish_single_delivery
from qa_parser.result import user_result
from test_core import p, write_docx


def test_final_customer_zip_distinguishes_policy_skip_and_mixed_templates(tmp_path):
    body = p('1．选择颜色') + p('A．红 B．蓝 C．绿 D．黄') + p('【答案】A')
    body += p('2．颜色是____。') + p('【答案】红')
    body += p('3．填写表格。') + '<w:tbl><w:tr><w:tc>' + p('a________') + '</w:tc></w:tr></w:tbl>' + p('【答案】蓝')
    source = write_docx(tmp_path / '01.docx', body)
    work = tmp_path / 'work'
    summary = run_document(source, 'auto', work)
    assert summary['counts']['policy_skipped'] == 1
    result = publish_single_delivery(summary, work)
    with ZipFile(result['delivery']) as archive:
        report = archive.read('_说明/0001/结果说明.txt').decode()
        assert '按策略跳过 1' in report and '失败或待复核 0' in report
        assert '多工作表导入尚未验证' in report
        assert '01.xlsx' in archive.namelist()


def test_policy_skip_does_not_hide_other_faults():
    issues = [{'code': 'table_interaction_skipped', 'policy_skip': True}, {'code': 'formula_review'}]
    quarantine = {'questions': [{'id': 'q1', 'number': '1', 'issues': issues}]}
    result = user_result({'passed': 0, 'total': 1, 'status': 'completed_with_quarantine'}, quarantine)
    question = result['affected_questions'][0]
    assert question['policy_skipped']
    assert len(question['reasons']) == 2
    assert any('公式' in reason for reason in question['reasons'])
