#!/usr/bin/env python3
"""Local-only real-corpus audit. Never run this on CI or upload its outputs.

Compare the same source bytes with a preserved baseline; count document XML
duplicates separately, never count answer markers as source questions.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from qa_parser.classification import classify_document
from qa_parser.core import run_document, load_task, validate_task
from qa_parser.export_preview import build_export_preview


def audit(source, target, baseline):
    classification = classify_document(source)
    result = run_document(source, 'auto', target)
    task, directory = load_task(target / 'task')
    report = validate_task(task, directory, source)
    assert report == json.loads((directory / 'report.json').read_text())
    assert result['status'] != 'failed', result
    qmap = {q['number']: q for q in report['questions']}
    assert len(qmap) == len(report['questions']), 'Duplicate source numbers'
    by_issue = {}
    for q in report['questions']:
        for code in sorted({i['code'] for i in q['issues']}):
            by_issue.setdefault(code, []).append(q['number'])
    if result['passed']:
        preview = build_export_preview(target / 'export')
        assert preview['counts']['questions'] == result['passed']
    row = {'source': source.name, 'source_sha256': task['source']['sha256'],
           'classification': {k: classification[k] for k in ('status', 'kind', 'total', 'counts')},
           'status': result['status'], 'counts': result['counts'], 'issues': by_issue,
           'omitted': [{'number': q['number'], 'parent_id': q.get('parent_id'),
                        'issues': q['issues']} for q in report['questions'] if q['issues']],
           'knowledge_rows': sum(bool(q.get('knowledge_point')) and not q['issues'] for q in report['questions']),
           'table_roles': dict(Counter(t['role'] for q in report['questions'] for t in q.get('table_roles', [])))}
    with ZipFile(source) as z:
        row['body_xml_sha256'] = hashlib.sha256(z.read('word/document.xml')).hexdigest()
    oldfile = baseline / source.stem / 'task/task.json'
    if oldfile.exists():
        old = json.loads(oldfile.read_text())
        assert old['source']['sha256'] == task['source']['sha256']
        oldreport = json.loads((oldfile.parent / 'report.json').read_text())
        oldq = {q['number']: q for q in oldreport['questions']}
        fields = ['stem', 'options', 'answer', 'analysis', 'difficulty', 'knowledge_point', 'raw_text']
        changes = {field: [n for n, q in qmap.items() if n in oldq and q.get(field) != oldq[n].get(field)] for field in fields}
        oldform = [(f['source']['sha256'], f.get('latex')) for f in old['formulas']]
        newform = [(f['source']['sha256'], f.get('latex')) for f in task['formulas']]
        assert oldform == newform, 'Formula source/conversion drift'
        original_images = lambda t: [a['sha256'] for a in t['assets'] if not a.get('origin', '').startswith('formula')]
        assert original_images(old) == original_images(task), 'Original image drift'
        row['baseline'] = {'total': len(oldq), 'passed': oldreport['passed'],
                           'new_source_numbers': sorted(set(qmap) - set(oldq)),
                           'removed_source_numbers': sorted(set(oldq) - set(qmap)),
                           'field_changes': changes,
                           'new_omissions': [n for n,q in qmap.items() if q['issues'] and n in oldq and not oldq[n]['issues']],
                           'formula_source_and_latex_unchanged': True, 'original_images_unchanged': True}
        if source.parent.name != '复合':
            assert len(qmap) == 100
            assert all(not changes[k] for k in ('stem', 'options', 'analysis', 'difficulty', 'raw_text')), changes
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, default=ROOT / 'reference')
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    rows = []
    for source in sorted(args.reference.glob('*.docx')) + sorted((args.reference / '复合').glob('*.docx')):
        row = audit(source, args.out / source.stem, args.baseline)
        row['corpus'] = 'new' if source.parent.name == '复合' else 'original'
        rows.append(row)
        print(json.dumps({k: row[k] for k in ('source', 'counts', 'issues')}, ensure_ascii=False), flush=True)
    summary = {'scope': 'private real corpus on local host only', 'customer_app': 'unverified', 'documents': rows}
    for corpus in ('original', 'new'):
        selected = [r for r in rows if r['corpus'] == corpus]
        unique = {r['body_xml_sha256']: r for r in selected}
        summary[corpus] = {'files': len(selected), 'unique_body_xml': len(unique),
            'counts_including_duplicates': dict(Counter({k: sum(r['counts'][k] for r in selected) for k in selected[0]['counts']})),
            'counts_unique_body_xml': {k: sum(r['counts'][k] for r in unique.values()) for k in selected[0]['counts']}}
    (args.out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps({k: summary[k] for k in ('original', 'new')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
