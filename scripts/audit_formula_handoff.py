#!/usr/bin/env python3
"""Developer-only source/task/XLSX handoff evidence; never rewrites a task.

The underscore experiment is deliberately an illustrative unsafe transform,
NOT a Markdown implementation and NOT evidence of the customer's pipeline.
Private source excerpts stay in the explicitly selected local output folder.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

from qa_parser.core import load_task, safe_file
from qa_parser.export_preview import _workbook_rows
from qa_parser.formula_images import preview_to_png


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', type=Path, required=True)
    parser.add_argument('--export', type=Path)
    parser.add_argument('--numbers', nargs='+', default=['57', '92'])
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--probe-images', action='store_true')
    args = parser.parse_args()
    task, root = load_task(args.task)
    args.out.mkdir(parents=True, exist_ok=False)
    cells = {}
    if args.export:
        rows = _workbook_rows(args.export / 'questions.xlsx')
        columns = rows[0][1]
        manifest = json.loads((args.export / 'report.json').read_text())
        cells = {str(mapping['number']): dict(zip(columns, rows[i + 1][1]))
                 for i, mapping in enumerate(manifest['rows'])}
    fmap = {f['id']: f for f in task['formulas']}
    results, display = [], []
    for number in args.numbers:
        q = next(q for q in task['questions'] if str(q['number']) == number)
        formulas = []
        for fid in q['formula_ids']:
            f = fmap[fid]
            original = safe_file(root, f['source']['path']).read_bytes()
            assert hashlib.sha256(original).hexdigest() == f['source']['sha256']
            row = {key: f.get(key) for key in ['id', 'format', 'location', 'status', 'latex', 'source', 'preview_binding']}
            latex = f.get('latex')
            if latex:
                display.append({'number': number, 'id': fid, 'latex': latex})
            # Probe the electrical equality/fraction formulas in the screenshots,
            # rather than inferring feasibility from one trivial subscript image.
            if args.probe_images and latex and '=' in latex and 'R' in latex:
                preview = f.get('preview')
                if preview and (f.get('preview_binding') or {}).get('verified'):
                    path = safe_file(root, preview['path'])
                    data = path.read_bytes()
                    assert hashlib.sha256(data).hexdigest() == preview['sha256']
                    converted = preview_to_png(data, path.suffix)
                    row['image_probe'] = {k: v for k, v in converted.items() if k != 'png'}
                    if converted['status'] == 'converted':
                        filename = fid + '.png'
                        (args.out / filename).write_bytes(converted['png'])
                        row['image_probe']['local_image'] = filename
                        row['image_probe']['source_preview_sha256'] = preview['sha256']
                else:
                    row['image_probe'] = {'status': 'unavailable', 'reason': 'No verified bound original preview'}
            formulas.append(row)
        excel = cells.get(number, {}).get('解析')
        results.append({'number': number, 'task_analysis': q['analysis'], 'excel_analysis': excel,
                        'excel_matches_task': excel == q['analysis'] if excel is not None else None,
                        'literal_em_in_task': '<em>' in q['analysis'],
                        'literal_em_in_excel': '<em>' in excel if excel is not None else None,
                        'formula_objects': formulas})
    examples = [r'U=I_2R_0', r'I_1R_x=I_2R_0', r'R_x=\frac{I_2R_0}{I_1}',
                r'R_x=\frac{I_2R_0}{(I_1-I_2)}']
    experiments = []
    for latex in examples:
        wrapped = '$$' + latex + '$$'
        polluted = re.sub(r'_([^_]+)_', r'<em>\1</em>', wrapped)
        experiments.append({'raw_latex': latex, 'excel_expected': wrapped,
                            'illustrative_unsafe_emphasis_result': polluted,
                            'underscore_count_before': wrapped.count('_'),
                            'underscore_count_after': polluted.count('_'),
                            'intended_delimiter_matches': re.findall(r'\$\$(.*?)\$\$', wrapped),
                            'wrong_single_dollar_split': wrapped.split('$'),
                            'note': 'Illustrative only. A real Markdown engine may behave differently; customer engine unknown.'})
    report = {'schema_version': 1, 'source': task['source'], 'questions': results,
              'synthetic_boundary_experiments': experiments,
              'limitations': ['Not the customer supplied XLSX', 'Customer importer and renderer unavailable',
                              'Successful math syntax rendering is not semantic fidelity or APP acceptance',
                              'Image probes do not change any default or exported question']}
    (args.out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    (args.out / 'display-input.json').write_text(json.dumps(display, ensure_ascii=False, indent=2))
    print(json.dumps({'questions': len(results), 'formulas': len(display), 'output': str(args.out)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
