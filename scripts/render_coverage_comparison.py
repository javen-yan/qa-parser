#!/usr/bin/env python3
"""Generate an offline source/actual-export comparison; never launch a UI.

Example:
  .venv/bin/python scripts/render_coverage_comparison.py \
    --batch outputs/coverage-final/batch --out output/coverage/comparison.html

The top question is read from its actual XLSX row via export/report.json.
Additional formulas are selected from the provided batch's parsed tasks; no
formula text is rewritten or supplied as a hardcoded expected answer.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from qa_parser.problem_preview import _embedded_math
from check_reference import read_xlsx

DEFAULT_SCREENSHOT = Path('/var/folders/g4/nbvb33hj6bjckmgdng8dqjp00000gn/T/codex-clipboard-0bfdf87d-0df9-42cf-8650-0cacdcf52d54.png')
ESC = lambda value: html.escape(str(value), quote=True)


def math_span(latex):
    return '<span class="known-formula" data-latex="' + ESC(latex) + '">' + ESC('$$' + latex + '$$') + '</span>'


def rich_text(value):
    """Only balanced $$ delimiters become math; all other content stays text."""
    output = []
    cursor = 0
    for match in re.finditer(r'\$\$(.*?)\$\$', str(value), flags=re.S):
        output.append(ESC(str(value)[cursor:match.start()]))
        output.append(math_span(match.group(1)))
        cursor = match.end()
    output.append(ESC(str(value)[cursor:]))
    return ''.join(output)


def read_documents(batch):
    summary = json.loads((batch / 'batch-summary.json').read_text(encoding='utf-8'))
    documents = []
    for row in summary['files']:
        folder = Path(row['output'])
        if not folder.is_absolute():
            folder = batch / folder
        task_file = folder / 'task/task.json'
        report_file = folder / 'export/report.json'
        if not task_file.is_file() or not report_file.is_file():
            continue
        task = json.loads(task_file.read_text(encoding='utf-8'))
        report = json.loads(report_file.read_text(encoding='utf-8'))
        documents.append({'row': row, 'folder': folder, 'task': task, 'report': report})
    return summary, documents


def actual_question(doc, number):
    source = next(q for q in doc['task']['questions'] if str(q['number']) == str(number))
    mapping = next((r for r in doc['report']['rows'] if r['id'] == source['id']), None)
    if mapping is None:
        raise ValueError(f'Comparison question {number} was not exported; refusing to substitute task content')
    workbook = doc['folder'] / 'export/questions.xlsx'
    cells = read_xlsx(workbook)
    values = cells[mapping['excel_row'] - 1]
    fields = dict(zip(cells[0], values))
    for column in ('题目', '答案', 'optionA', 'optionB', 'optionC', 'optionD'):
        if column not in fields:
            raise ValueError('Actual workbook lacks required comparison column: ' + column)
    return source, mapping, fields, hashlib.sha256(workbook.read_bytes()).hexdigest()


def select_examples(documents):
    candidates = []
    for doc in documents:
        exported = {r['id'] for r in doc['report']['rows']}
        for formula in doc['task']['formulas']:
            if formula.get('status') != 'converted' or not formula.get('latex'):
                continue
            questions = [q for q in doc['task']['questions'] if formula['id'] in q.get('formula_ids', [])]
            candidates.append((doc, formula, questions, exported))
    selectors = [
        ('括号与下标组合', lambda s: 'Fe' in s and 'NO' in s and '(' in s and '_' in s),
        ('条件中的催化剂下标', lambda s: 'xrightarrow' in s and 'MnO' in s and '_2' in s),
        ('条件中的温度下标', lambda s: 'xrightarrow' in s and 'T_1' in s and (r'\mathrm{C}' in s or '℃' in s)),
        ('原始箭头与多行结构', lambda s: 'underrightarrow' in s and 'begin{array}' in s),
        ('矩阵或多行条件', lambda s: 'begin{array}' in s and 'underrightarrow' not in s),
        ('上下条件完整保留', lambda s: 'underset' in s or 'xrightarrow[' in s or (r'\\ \Delta' in s and '催化剂' in s)),
        ('显式字体比例符号', lambda s: r'\mathrel{:}' in s),
        ('化合价位于元素上方', lambda s: r'\overset{+2}{Mg}' in s),
        ('原文的前置上标位置', lambda s: '{}^{2}R' in s),
    ]
    selected = []; used = set()
    for label, predicate in selectors:
        match = next((candidate for candidate in candidates
                      if (candidate[0]['row']['name'], candidate[1]['id']) not in used
                      and predicate(candidate[1]['latex'])), None)
        if match is None:
            continue
        doc, formula, questions, exported = match
        used.add((doc['row']['name'], formula['id']))
        selected.append({'label': label, 'source_name': doc['row']['name'],
                         'formula_id': formula['id'], 'latex': formula['latex'],
                         'location': formula['location'], 'source_sha256': formula['source']['sha256'],
                         'questions': [{'number': q['number'], 'exported': q['id'] in exported} for q in questions]})
    return selected


def render(batch, screenshot, output):
    summary, documents = read_documents(batch)
    doc = next(d for d in documents if d['row']['name'] == 'MathType公式化学2.docx')
    question, mapping, fields, workbook_sha = actual_question(doc, '1')
    original = screenshot.read_bytes()
    if len(original) > 16 * 1024 * 1024 or not original.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('Expected a bounded original PNG screenshot')
    original_src = 'data:image/png;base64,' + base64.b64encode(original).decode('ascii')
    css, scripts = _embedded_math()
    if not scripts:
        raise ValueError('Pinned offline math assets unavailable')
    script_policy = ' '.join("'sha256-" + base64.b64encode(hashlib.sha256(s.encode()).digest()).decode() + "'" for s in scripts)
    policy = "default-src 'none'; img-src data:; font-src data:; script-src " + script_policy + "; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
    examples = select_examples(documents)
    chunks = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
              '<meta name="viewport" content="width=device-width,initial-scale=1">',
              '<meta http-equiv="Content-Security-Policy" content="' + ESC(policy) + '">',
              '<title>原始公式与实际导出对照</title><style>' + css + '</style>',
              '''<style>
*{box-sizing:border-box}body{margin:0;background:#f5f6f4;color:#26302d;font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}main{max-width:1080px;margin:auto;padding:20px 22px 50px}h1{font-size:24px;line-height:1.3;margin:0 0 5px}h2{font-size:17px;line-height:1.35;margin:0 0 9px}p{margin:6px 0}.muted,figcaption{font-size:12px;color:#6a746e}header{margin-bottom:14px}.badge{float:right;background:#e3efe6;border:1px solid #bad4c0;color:#2e6040;font-size:13px;padding:5px 10px;border-radius:20px}.panel{background:#fff;border:1px solid #dce2dc;border-radius:12px;padding:14px 18px;margin-bottom:14px}figure{margin:0}.source-image{display:block;width:100%;height:auto}.output-stem{font-size:17px;font-weight:500;margin:4px 0 12px}.options{display:grid;grid-template-columns:1fr 1fr;column-gap:30px;row-gap:8px}.option{display:flex;align-items:center;min-height:40px;gap:9px}.letter{color:#366d4d;font-weight:700;flex:none}.option-text{white-space:pre-wrap;overflow-wrap:anywhere}.known-formula{display:inline-block;max-width:100%;vertical-align:middle;overflow-x:auto}.answer{font-size:13px;margin-top:8px;color:#56645b}.example-list{display:grid;grid-template-columns:1fr 1fr;gap:14px}.example{margin:0;min-width:0}.example-math{padding:12px 0;font-size:18px;overflow-x:auto}details{margin-top:9px;font-size:12px;color:#5b655f}summary{cursor:pointer}pre,code{white-space:pre-wrap;overflow-wrap:anywhere}pre{background:#f3f5f1;padding:10px;border-radius:6px}.section-title{margin:26px 0 12px}.note{padding:10px 14px;border-left:3px solid #789b82;background:#eef3ee;font-size:13px;margin:16px 0}.evidence{font-size:12px;color:#68746b;word-break:break-all}@media(max-width:760px){main{padding:14px}.options,.example-list{grid-template-columns:1fr}.badge{float:none;display:inline-block;margin-bottom:8px}.panel{padding:13px}}
</style></head><body><main>''',
              '<header><span class="badge">源第 1 题 · 已导出</span><h1>条件箭头：原文与实际导出对照</h1>',
              '<p class="muted">MathType公式化学2.docx · 直接读取当前批次 Excel，所有公式均来自原始对象解析。</p></header>',
              '<section class="panel"><h2>原文截图</h2><figure><img class="source-image" alt="用户提供的原文截图：第1题四个选项" src="' + original_src + '"></figure></section>',
              '<section class="panel"><h2>实际 Excel 内容 · 离线公式预览</h2>',
              '<div class="output-stem">' + rich_text(fields['题目']) + '</div><div class="options">']
    for letter in 'ABCD':
        chunks.append('<div class="option"><span class="letter">' + letter + '.</span><div class="option-text">' + rich_text(fields['option' + letter]) + '</div></div>')
    chunks.extend(['</div><p class="answer">原文答案：' + ESC(fields['答案']) + ' · Excel 第 ' + ESC(mapping['excel_row']) + ' 行</p>',
                   '<details><summary>查看实际导出的公式文本</summary><pre>' + ESC('\n'.join(letter + '. ' + fields['option' + letter] for letter in 'ABCD')) + '</pre></details></section>',
                   '<p class="note">上图用于开发核对；解析过程读取原 DOCX 结构，不依赖截图。这里只展示表达式是否完整保留，不替原文纠错，也不代表目标 APP 的导入验收。</p>',
                   '<h2 class="section-title">同批次真实公式代表</h2><div class="example-list">'])
    for example in examples:
        qlabels = '、'.join(str(q['number']) + ('（已导出）' if q['exported'] else '（所属题仍待复核）') for q in example['questions']) or '未关联题目'
        chunks.extend(['<article class="panel example"><h2>' + ESC(example['label']) + '</h2>',
                       '<p class="muted">' + ESC(example['source_name']) + ' · 源第 ' + ESC(qlabels) + ' 题</p>',
                       '<div class="example-math">' + math_span(example['latex']) + '</div>',
                       '<details><summary>公式源码与源定位</summary><pre>' + ESC(example['latex']) + '</pre><p class="evidence">' + ESC(example['formula_id'] + ' · ' + example['location']) + '</p></details></article>'])
    chunks.extend(['</div><details><summary>批次与证据校验值</summary><pre>' + ESC(json.dumps({
        'total_files': summary.get('total_files'), 'exported': summary.get('passed'), 'quarantined': summary.get('quarantined'),
        'source_sha256': doc['task']['source']['sha256'], 'xlsx_sha256': workbook_sha,
        'original_screenshot_sha256': hashlib.sha256(original).hexdigest(),
    }, ensure_ascii=False, indent=2)) + '</pre></details></main>'])
    chunks.extend('<script>' + script + '</script>' for script in scripts)
    chunks.append('</body></html>')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('\n'.join(chunks), encoding='utf-8')
    manifest = {'schema_version': 1, 'batch': str(batch), 'html': str(output), 'source_question': question['number'],
                'excel_row': mapping['excel_row'], 'source_sha256': doc['task']['source']['sha256'],
                'xlsx_sha256': workbook_sha, 'question_fields': fields, 'examples': examples,
                'html_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
                'screenshot_sha256': hashlib.sha256(original).hexdigest(), 'scripts': len(scripts), 'network_required': False}
    output.with_suffix('.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--screenshot', type=Path, default=DEFAULT_SCREENSHOT)
    args = parser.parse_args()
    manifest = render(args.batch.resolve(), args.screenshot.resolve(), args.out.resolve())
    print(json.dumps({'html': manifest['html'], 'examples': len(manifest['examples']),
                      'excel_row': manifest['excel_row'], 'sha256': manifest['html_sha256']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
