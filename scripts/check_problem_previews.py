#!/usr/bin/env python3
"""Verify the nine-sample explanation regression without launching a browser.

Use --executable /path/to/App/Contents/MacOS/App to check a frozen distribution.
This verifies conversion semantics, exact problem anchors and offline payloads;
it does not replace native-window interaction or customer APP acceptance.
"""
from __future__ import annotations
import argparse
import base64
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from zipfile import ZipFile

from lxml import html

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from qa_parser.batch import run_batch
from qa_parser.problem_preview import build_problem_preview, render_preview_html
from check_reference import read_xlsx


def check(value, message):
    if not value:
        raise AssertionError(message)


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def images(folder):
    return {p.name: sha(p.read_bytes()) for p in sorted((folder / 'images').glob('*')) if p.is_file()}


def check_html(text, question_count, problem_count):
    document = html.fromstring(text)
    check(len(document.xpath('//article')) == question_count, 'HTML question count differs')
    check(len(document.xpath('//div[@class="problem"]')) == problem_count, 'HTML problem count differs')
    check(not document.xpath('//iframe|//object|//embed|//link|//form'), 'Unexpected external/active HTML element')
    for node in document.iter():
        check(not any(key.lower().startswith('on') for key in node.attrib), 'Event handler attribute in preview')
    for node in document.xpath('//*[@src]'):
        check(node.tag == 'img' and node.get('src').startswith('data:image/png;base64,'), 'Non-inline image/script resource')
        png = base64.b64decode(node.get('src').split(',', 1)[1], validate=True)
        check(png.startswith(b'\x89PNG\r\n\x1a\n'), 'Inline image is not PNG')
    for node in document.xpath('//*[@href]'):
        check(node.tag == 'a' and node.get('href').startswith('#'), 'External link in standalone preview')
        check(document.get_element_by_id(node.get('href')[1:], None) is not None, 'Broken internal question link')
    policies = document.xpath('//meta[@http-equiv="Content-Security-Policy"]/@content')
    check(len(policies) == 1, 'Missing preview CSP')
    policy = policies[0]
    for restriction in ("default-src 'none'", 'img-src data:', 'font-src data:', "base-uri 'none'", "form-action 'none'"):
        check(restriction in policy, 'Missing CSP restriction: ' + restriction)
    scripts = document.xpath('//script')
    check(len(scripts) == 4, 'KaTeX or runner unavailable; expected offline runtime')
    for script in scripts:
        value = script.text or ''
        digest = base64.b64encode(hashlib.sha256(value.encode()).digest()).decode()
        check("'sha256-" + digest + "'" in policy, 'Inline script not covered by CSP hash')
    fonts = []
    for style in document.xpath('//style'):
        for url in re.findall(r'url\(([^)]+)\)', style.text or ''):
            check(url.startswith(('data:font/woff2;base64,', 'data:font/otf;base64,')), 'Non-inline font dependency')
            decoded = base64.b64decode(url.split(',', 1)[1], validate=True)
            check(decoded.startswith((b'wOF2', b'OTTO')), 'Bad bundled WOFF2 data')
            fonts.append(sha(decoded))
    check(len(set(fonts)) >= 20, 'Offline font payload incomplete')
    check('file://' not in text and '&lt;w:object' not in text and '&lt;w:document' not in text,
          'Source document/path leaked into portable explanation')
    return {'inline_images': len(document.xpath('//img')), 'font_faces': len(set(fonts)),
            'known_formula_spans': len(document.xpath('//span[@class="known-formula"]'))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, default=ROOT / 'outputs/bundled-all-nine')
    parser.add_argument('--executable', type=Path)
    parser.add_argument('--restricted-path', action='store_true', help='Frozen binary PATH=/usr/bin:/bin')
    args = parser.parse_args()
    out = args.out.resolve(); out.mkdir(parents=True, exist_ok=False)
    baseline = args.baseline.resolve()
    sources = sorted((ROOT / 'reference').glob('*.docx'))
    baseline_folders = {json.loads(p.read_text())['source']['name']: p.parent.parent
                        for p in baseline.glob('*/task/task.json')}
    check(len(sources) == len(baseline_folders) == 9, 'Expected fixed nine sample documents')
    if args.executable:
        environment = dict(os.environ)
        if args.restricted_path: environment['PATH'] = '/usr/bin:/bin'
        command = [str(args.executable.resolve()), '--cli', 'batch', *map(str, sources),
                   '--mode', 'auto', '--out', str(out / 'batch')]
        completed = subprocess.run(command, capture_output=True, text=True, env=environment, timeout=180)
        (out / 'cli.stdout.txt').write_text(completed.stdout, encoding='utf-8')
        (out / 'cli.stderr.txt').write_text(completed.stderr, encoding='utf-8')
        check(completed.returncode == 2, f'Expected partial-failure exit2, got {completed.returncode}')
        result = json.loads((out / 'batch/batch-summary.json').read_text())
    else:
        result = run_batch(sources, 'auto', out / 'batch')
    check((result['passed'], result['quarantined']) == (831, 69), 'Conversion counts drifted')
    files = []; total_questions = total_problems = html_count = 0; latex = []; position_counts = Counter()
    for row in result['files']:
        folder = Path(row['output']); expected = baseline_folders[row['name']]
        actual_cells = read_xlsx(folder / 'export/questions.xlsx')
        check(actual_cells == read_xlsx(expected / 'export/questions.xlsx'), 'Excel changed: ' + row['name'])
        check(images(folder / 'export') == images(expected / 'export'), 'Images changed: ' + row['name'])
        actual_quarantine = json.loads((folder / 'quarantine.json').read_text())
        expected_quarantine = json.loads((expected / 'quarantine.json').read_text())
        check(actual_quarantine == expected_quarantine, 'Raw quarantine changed: ' + row['name'])
        check(json.loads((folder/'export/report.json').read_text())['omitted_questions'] ==
              json.loads((expected/'export/report.json').read_text())['omitted_questions'], 'Export omissions changed')
        preview = build_problem_preview(folder / 'task', row['name'])
        check(not preview['document_issues'], 'Unexpected document preview issue')
        check([(q['id'],q['number']) for q in preview['questions']] ==
              [(q['id'],q['number']) for q in actual_quarantine['questions']], 'Preview questions drifted')
        task = json.loads((folder/'task/task.json').read_text()); formulas = {f['id']:f for f in task['formulas']}
        q_by_id = {q['id']:q for q in actual_quarantine['questions']}
        per_file_problems=0
        for q in preview['questions']:
            known = {p['id']:p for p in q['problems']}; occurrences=Counter()
            expected_failed = {i['formula_id'] for i in q_by_id[q['id']]['issues'] if i.get('formula_id')}
            anchored_failed=set()
            for field in q['fields']:
                for segment in field['segments']:
                    if segment['type']=='latex': latex.append(segment['latex'])
                    for pid in segment.get('problem_ids',[]):
                        check(pid in known, 'Unknown problem anchor')
                        check(known[pid]['field']==field['key'], 'Problem points to wrong field')
                        check(known[pid]['source']==segment.get('source'), 'Problem points to wrong source location')
                        occurrences[pid]+=1;anchored_failed.add(segment['ref_id'])
                        check(segment['type']=='placeholder', 'Rejected formula displayed as successful image/formula')
                        check(formulas[segment['ref_id']]['status']=='review', 'Success formula erroneously marked')
            check(set(known)==set(occurrences) and all(v==1 for v in occurrences.values()), 'Missing or duplicated inline marker')
            check(anchored_failed==expected_failed, 'Failed source objects not exactly anchored')
            check(len(q['problems'])==len(expected_failed), 'Word script duplicate not merged')
            for problem in q['problems']:
                check(problem['field']!='question', 'Actual formula lacks specific field')
                position_counts[problem['field']]+=1
                check(problem['reason'] and problem['impact'] and problem['source_label'], 'Unexplained problem')
                check(problem['reason'] in (folder/'result.txt').read_text(), 'Detailed reason absent from customer TXT')
                check(problem['field_label'] in (folder/'result.txt').read_text(), 'Field label absent from customer TXT')
            per_file_problems+=len(q['problems'])
        item={'name':row['name'],'questions':len(preview['questions']),'problems':per_file_problems,
              'exported':row['passed'],'excel_cells_unchanged':True,'images_unchanged':True,'raw_quarantine_unchanged':True}
        if preview['questions']:
            check(row.get('problem_preview'), 'Missing generated portable preview')
            text=Path(row['problem_preview']).read_text()
            check(text==render_preview_html(preview), 'Generated portable preview differs from current view')
            check(sha(text.encode())==row['problem_preview_sha256'], 'Preview hash mismatch')
            item.update(check_html(text,len(preview['questions']),per_file_problems));html_count+=1
        else:
            check(not row.get('problem_preview'), 'Unneeded empty problem HTML for no-problem file')
            check('没有未导出' in preview['empty_message'], 'No-problem file explanation unclear')
        files.append(item);total_questions+=len(preview['questions']);total_problems+=per_file_problems
    check((total_questions,total_problems,html_count)==(69,146,6),'Fixed baseline preview counts differ')
    # Portable final bundle explicitly excludes snapshots, diagnostics and evidence.
    with ZipFile(result['delivery']) as archive:
        check(archive.testzip() is None,'ZIP integrity failure')
        names=archive.namelist()
        check(sum(n.endswith('.xlsx') for n in names)==9,'Missing final workbooks')
        check(sum(n.endswith('.html') for n in names)==6,'Missing portable problem explanations')
        check(not any(Path(n).suffix.lower() in {'.json','.docx','.bin','.wmf','.tex','.xml','.log'} for n in names),'Internal file leaked')
        check(not any(part in {'task','evidence','source','.inputs'} for n in names for part in Path(n).parts),'Internal folder leaked')
        archive.extractall(out/'extracted')
    refs=0
    for workbook in sorted((out/'extracted').glob('*/questions.xlsx')):
        for row in read_xlsx(workbook):
            for cell in row:
                for image in re.findall(r'请参考 (\d{4,}\.[A-Za-z0-9]+)',cell):
                    check((workbook.parent/'images'/image).is_file(),'Unresolved extracted Excel image reference');refs+=1
    # Exercise the actual vendored runtime without a browser/network dependency.
    save(out/'known-formulas.json',latex)
    node_script='''const fs=require('fs');const katex=require(process.argv[1]);const values=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));let failures=[];values.forEach((v,i)=>{try{katex.renderToString(v,{throwOnError:true,trust:false,strict:'error',maxExpand:1000,maxSize:20,displayMode:false})}catch(e){failures.push({index:i,latex:v,error:e.message})}});console.log(JSON.stringify({total:values.length,rendered:values.length-failures.length,explicit_source_fallbacks:failures}));'''
    node = subprocess.run(['node','-e',node_script,str(ROOT/'src/qa_parser/web/vendor/katex/katex.min.js'),str(out/'known-formulas.json')],capture_output=True,text=True,timeout=30)
    check(node.returncode==0,'Vendored KaTeX could not execute: '+node.stderr)
    math=json.loads(node.stdout);save(out/'katex-render-check.json',math)
    report={'status':'passed','executable':str(args.executable) if args.executable else 'development core',
            'passed':831,'quarantined':69,'problem_questions':total_questions,'unique_problem_positions':total_problems,
            'standalone_html_files':html_count,'excel_files':9,'final_zip':result['delivery'],
            'extracted_image_reference_occurrences':refs,'position_fields':dict(position_counts),
            'katex':math,'files':files,'unverified':['native window interaction','Windows','customer APP import/rendering']}
    save(out/'validation.json',report)
    print(json.dumps({k:v for k,v in report.items() if k not in {'files','katex'}},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
