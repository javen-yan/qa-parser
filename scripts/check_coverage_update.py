#!/usr/bin/env python3
"""Audit coverage gains against the fixed 831/69 baseline in a NEW directory.

--executable runs the frozen application's CLI with PATH=/usr/bin:/bin.
--compare accepts a development audit output or its batch directory and requires
identical XLSX cells, image bytes, task semantics and processing/export reports.
This is automated evidence, not Windows, native UI or customer APP acceptance.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from qa_parser.batch import run_batch
from qa_parser.core import load_task, validate_task
from qa_parser.problem_preview import build_problem_preview
from check_reference import check_ownership, check_export, check_fallbacks, comparable, read_xlsx
from check_problem_previews import check_html, images


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def folders(root):
    if (root / 'batch').is_dir():
        root = root / 'batch'
    return {read(p)['source']['name']: p.parent.parent for p in root.glob('*/task/task.json')}


def source_free(value):
    result = deepcopy(value)
    if isinstance(result.get('source'), dict):
        result['source'].pop('original_path', None)
    return result


def formula_changes(before, after):
    """IDs can shift when Word run groups merge; match immutable source instead."""
    exact = {(f['location'], f['source']['sha256']): f for f in after['formulas']}
    grouped = defaultdict(list)
    for f in after['formulas']:
        if f.get('origin') == 'word_group_script_sequence':
            for location in f.get('source_locations', []):
                grouped[location].append(f)
    question_numbers = defaultdict(list)
    for question in before['questions']:
        for fid in question['formula_ids']:
            question_numbers[fid].append(question['number'])
    transitions = Counter(); changes = []; matched_ids = set()
    fields = ('status', 'latex', 'reason', 'fallback_error')
    for old in before['formulas']:
        new = exact.get((old['location'], old['source']['sha256']))
        mapping = 'location_and_source_sha256'
        if new is None:
            candidates = grouped[old['location']]
            check(old['format'] == 'latex' and len(candidates) == 1,
                  'Formula source disappeared or changed without explicit Word group lineage: ' + old['location'])
            new = candidates[0]; mapping = 'explicit_word_group_source_locations'
        matched_ids.add(new['id'])
        transitions[f"{old['status']} -> {new['status']}"] += 1
        if mapping != 'location_and_source_sha256' or any(old.get(k) != new.get(k) for k in fields):
            changes.append({'location': old['location'], 'question_numbers': question_numbers[old['id']],
                            'mapping': mapping, 'old_source_sha256': old['source']['sha256'],
                            'new_source_sha256': new['source']['sha256'],
                            'before': {k: old.get(k) for k in fields},
                            'after': {k: new.get(k) for k in fields},
                            'old_engine': old.get('engine'), 'new_engine': new.get('engine'),
                            'new_location': new['location'], 'new_origin': new.get('origin')})
    # New source objects are recorded separately, never hidden as a count delta.
    additions = [{'location': f['location'], 'source_sha256': f['source']['sha256'],
                  'status': f['status'], 'origin': f.get('origin')} for f in after['formulas'] if f['id'] not in matched_ids]
    return dict(transitions), changes, additions


def verify_preview(folder, row, quarantine):
    preview = build_problem_preview(folder / 'task', row['name'])
    check(not preview['document_issues'], 'Unexpected document-level preview failure')
    check([(q['id'], q['number']) for q in preview['questions']] ==
          [(q['id'], q['number']) for q in quarantine['questions']], 'Preview omission list differs')
    count = 0; categories = Counter()
    for q in preview['questions']:
        problems = {p['id']: p for p in q['problems']}; anchored = Counter()
        for field in q['fields']:
            for segment in field['segments']:
                for pid in segment.get('problem_ids', []):
                    check(pid in problems and problems[pid]['field'] == field['key'], 'Preview anchor points to wrong field')
                    check(problems[pid]['source'] == segment.get('source'), 'Preview anchor points to wrong source')
                    check(segment['type'] == 'placeholder', 'Unresolved object displayed as successful formula/image')
                    anchored[pid] += 1
        check(set(problems) == set(anchored) and all(n == 1 for n in anchored.values()), 'Missing/duplicate problem marker')
        for problem in problems.values():
            check(problem['reason'] and problem['impact'], 'Missing readable reason/impact')
            categories[problem['category']] += 1
        count += len(problems)
    stats = {}
    if preview['questions']:
        check(row.get('problem_preview'), 'Missing offline problem preview')
        path = Path(row['problem_preview'])
        check(sha(path) == row['problem_preview_sha256'], 'Offline preview hash mismatch')
        stats = check_html(path.read_text(encoding='utf-8'), len(preview['questions']), count)
    else:
        check(not row.get('problem_preview'), 'Empty success file has unexpected problem preview')
    return {'questions': len(preview['questions']), 'problem_positions': count,
            'categories': dict(categories), **stats}


def check_bundle(result, out, expected_html, expected_workbooks):
    with ZipFile(result['delivery']) as archive:
        check(archive.testzip() is None, 'Corrupt final ZIP')
        names = archive.namelist()
        for name in names:
            path = PurePosixPath(name)
            check(not path.is_absolute() and '..' not in path.parts and '\\' not in name, 'Unsafe ZIP entry')
            check(path.suffix.lower() not in {'.json', '.docx', '.bin', '.wmf', '.tex', '.xml', '.log'}, 'Internal file in final ZIP')
            check(not set(path.parts) & {'task', 'evidence', 'source', '.inputs'}, 'Internal directory in final ZIP')
        check(sum(n.endswith('.xlsx') for n in names) == expected_workbooks, 'Final workbook count differs')
        check(sum(n.endswith('/未导出题目.html') for n in names) == expected_html, 'Final offline preview count differs')
        check(sum(n.endswith('/题目预览.html') for n in names) == expected_workbooks, 'Missing exported question previews')
        check('_display/NotoSansCJKsc-Regular.otf' in names and '_display/OFL.txt' in names, 'Missing offline display font/license')
        archive.extractall(out / 'extracted')
    references = 0
    for workbook in (out / 'extracted').glob('*/questions.xlsx'):
        for row in read_xlsx(workbook):
            for cell in row:
                for image in re.findall(r'(?:请)?参考 ?(\d{4,}\.[A-Za-z0-9]+)', cell):
                    check((workbook.parent / 'images' / image).is_file(), 'Broken extracted image reference: ' + image)
                    references += 1
    return {'integrity': 'passed', 'internal_files_excluded': True, 'xlsx_files': expected_workbooks,
            'offline_html_files': expected_html, 'image_reference_occurrences': references}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, default=ROOT / 'outputs/bundled-all-nine')
    parser.add_argument('--executable', type=Path)
    parser.add_argument('--compare', type=Path)
    args = parser.parse_args(argv)
    out = args.out.resolve(); out.mkdir(parents=True, exist_ok=False)
    sources = sorted((ROOT / 'reference').glob('*.docx'))
    baseline = folders(args.baseline.resolve())
    check(len(sources) == len(baseline) == 9, 'Expected fixed nine reference documents')
    before_reports = {name: read(folder / 'task/report.json') for name, folder in baseline.items()}
    before_counts = {'passed': sum(r['passed'] for r in before_reports.values()),
                     'review': sum(r['review'] for r in before_reports.values())}
    check(before_counts == {'passed': 831, 'review': 69}, 'Baseline is not the frozen 831/69 reference')
    comparisons = folders(args.compare.resolve()) if args.compare else None
    if comparisons is not None:
        check(set(comparisons) == set(baseline), 'Comparison sample set differs')
    cli_code = None
    if args.executable:
        environment = {**os.environ, 'PATH': '/usr/bin:/bin'}
        command = [str(args.executable.resolve()), '--cli', 'batch', *map(str, sources),
                   '--mode', 'auto', '--out', str(out / 'batch')]
        completed = subprocess.run(command, capture_output=True, text=True, env=environment, timeout=600)
        (out / 'cli.stdout.txt').write_text(completed.stdout, encoding='utf-8')
        (out / 'cli.stderr.txt').write_text(completed.stderr, encoding='utf-8')
        cli_code = completed.returncode
        check(cli_code in (0, 2), f'CLI execution failed ({cli_code}): {completed.stderr[-1000:]}')
        result = read(out / 'batch/batch-summary.json')
        check(cli_code == {'success': 0, 'partial_failure': 2}.get(result['status']), 'CLI exit status disagrees with batch')
    else:
        result = run_batch(sources, 'auto', out / 'batch')
    check(result['completed'] == 9 and len(result['files']) == 9, 'Batch incomplete')
    check({row['name'] for row in result['files']} == set(baseline), 'Batch source set differs')
    documents = []; aggregate_transitions = Counter(); formula_totals = Counter(); html_count = 0
    for row in result['files']:
        name = row['name']; folder = Path(row['output']); task, task_dir = load_task(folder / 'task')
        report = read(task_dir / 'report.json'); old_task = read(baseline[name] / 'task/task.json'); old_report = before_reports[name]
        check(not report['document_issues'], name + ': unexpected document issues')
        check(task['source']['sha256'] == old_task['source']['sha256'] == sha(ROOT / 'reference' / name), name + ': source SHA drift')
        check(sha(task_dir / task['source']['snapshot']['path']) == task['source']['sha256'], 'Source snapshot changed')
        check(len(task['questions']) == report['total'] == row['total'] == 100, name + ': question count differs')
        check([q['number'] for q in task['questions']] == list(map(str, range(1, 101))), name + ': source numbering differs')
        check(all(q['answer'].strip() for q in task['questions']), name + ': answer missing')
        check_ownership(task, report)
        fallbacks = check_fallbacks(task, task_dir)
        check(validate_task(task, task_dir, ROOT / 'reference' / name) == report, 'Reload validation differs')
        for f in task['formulas']:
            check(sha(task_dir / f['source']['path']) == f['source']['sha256'], 'Formula evidence SHA mismatch')
            formula_totals[f['status']] += 1
        check(report['passed'] == row['passed'] and report['review'] == row['quarantined'], 'Row summary differs')
        exported = read(folder / 'export/report.json')
        check_export(task, report, exported, folder / 'export')
        old_passed = {q['number'] for q in old_report['questions'] if q['status'] == 'passed'}
        now_passed = {q['number'] for q in report['questions'] if q['status'] == 'passed'}
        check(old_passed <= now_passed, name + ': previously passed questions regressed: ' + str(sorted(old_passed - now_passed)))
        recovered = sorted(now_passed - old_passed, key=int)
        remaining = [{'number': q['number'], 'issues': q['issues']} for q in report['questions'] if q['status'] == 'review']
        quarantine = read(folder / 'quarantine.json')
        check([q['number'] for q in quarantine['questions']] == [q['number'] for q in remaining], 'Raw quarantine disagrees with report')
        transitions, changes, additions = formula_changes(old_task, task)
        aggregate_transitions.update(transitions)
        preview = verify_preview(folder, row, quarantine)
        html_count += bool(preview['questions'])
        compared = None
        if comparisons is not None:
            comparison = comparisons[name]
            other_task = read(comparison / 'task/task.json')
            check(comparable(task) == comparable(other_task), name + ': development/frozen task semantic difference')
            check(read_xlsx(folder / 'export/questions.xlsx') == read_xlsx(comparison / 'export/questions.xlsx'), name + ': development/frozen XLSX difference')
            check(images(folder / 'export') == images(comparison / 'export'), name + ': development/frozen image difference')
            for relative in ('task/report.json', 'export/report.json', 'quarantine.json'):
                check(source_free(read(folder / relative)) == source_free(read(comparison / relative)), name + ': development/frozen report difference: ' + relative)
            compared = 'task, XLSX cells, image bytes and reports identical'
        documents.append({'name': name, 'total': 100, 'before_passed': old_report['passed'], 'passed': report['passed'],
                          'review': report['review'], 'recovered_question_numbers': recovered, 'remaining_questions': remaining,
                          'old_passed_preserved': True, 'source_sha256': task['source']['sha256'],
                          'formula_totals': dict(Counter(f['status'] for f in task['formulas'])),
                          'formula_engines': dict(Counter(f.get('engine', '') for f in task['formulas'])),
                          'formula_transitions': transitions, 'formula_changes': changes, 'new_formula_objects': additions,
                          'verified_image_fallbacks': fallbacks, 'preview': preview, 'comparison': compared})
        print(json.dumps({'name': name, 'passed': report['passed'], 'review': report['review'], 'recovered': recovered}, ensure_ascii=False), flush=True)
    check(sum(d['passed'] for d in documents) == result['passed'], 'Aggregate passed count differs')
    check(sum(d['review'] for d in documents) == result['quarantined'], 'Aggregate review count differs')
    bundle = check_bundle(result, out, html_count, 9)
    evidence = {'schema_version': 1, 'status': 'passed', 'baseline': str(args.baseline.resolve()),
                'executable': str(args.executable.resolve()) if args.executable else 'development core',
                'restricted_path': bool(args.executable), 'cli_exit_code': cli_code,
                'before': {'total': 900, **before_counts},
                'after': {'total': 900, 'passed': result['passed'], 'review': result['quarantined']},
                'recovered_questions': result['passed'] - before_counts['passed'],
                'all_previously_passed_preserved': True, 'formula_totals': dict(formula_totals),
                'formula_transitions': dict(aggregate_transitions), 'bundle': bundle, 'final_zip': result['delivery'],
                'comparison': str(args.compare.resolve()) if args.compare else None, 'documents': documents,
                'unverified': ['native window interaction', 'formula visual semantics not certified by this script',
                               'Windows execution', 'customer APP import/rendering']}
    save(out / 'validation.json', evidence)
    print(json.dumps({k: v for k, v in evidence.items() if k != 'documents'}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
