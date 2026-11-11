"""Evidence-based special responses and loss-preserving unresolved handoff."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile, ZIP_DEFLATED


def enrich_special_question(q, blocks, config, formulas=None):
    """Promote only complete responses; failed ordinary blank alignment stays strict.

    Mixed responses require unique, matching subpart labels and successful binding
    of every textual blank. Images are retained as source responses, never OCR-filled.
    All resource/formula validation still runs after this structural decision.
    """
    import re
    from .blank_answers import BLANK, SUBPART, ANSWER_PART, _masked, enrich_blank_question
    codes = {i['code'] for i in q['issues']}
    if not codes or codes - {'unsupported_kind', 'blank_answer_alignment'}:
        return
    stem, answer = q.get('stem', ''), q.get('original_answer', q.get('answer', ''))
    if not stem.strip() or not answer.strip() or q.get('options'):
        return
    subtype = None
    evidence = q.get('classification_evidence', [])
    if codes == {'unsupported_kind'}:
        if evidence != ['有独立答案但无选择选项或填空位置，作答方式暂未支持'] or BLANK.search(_masked(stem)):
            return
        subtype = '图示作答' if re.search(r'⟦I\d{6}⟧', stem + answer) else '简答'
    elif not BLANK.search(_masked(stem)):
        if re.search(r'⟦I\d{6}⟧', stem):
            subtype = '图内填空'
    else:
        parts, answers = list(SUBPART.finditer(stem)), list(ANSWER_PART.finditer(answer))
        labels = [m[1] for m in parts]
        if not labels or len(set(labels)) != len(labels) or labels != [m[1] for m in answers]:
            return
        if answer[:answers[0].start()].strip() or BLANK.search(_masked(stem[:parts[0].start()])):
            return
        has_blank = has_open = False
        for i, (part, response) in enumerate(zip(parts, answers)):
            text = stem[part.end():parts[i+1].start() if i+1 < len(parts) else len(stem)]
            value = answer[response.end():answers[i+1].start() if i+1 < len(answers) else len(answer)]
            if not text.strip() or not value.strip():
                return
            if BLANK.search(_masked(text)):
                child = {'kind': 'blank', 'stem': text, 'answer': value, 'issues': [], 'id': q['id'],
                         'location': q['location'], 'block_indices': []}
                enrich_blank_question(child, [], config, formulas=formulas)
                if child['issues']:
                    return
                has_blank = True
            else:
                has_open = True
        if has_blank and has_open:
            subtype = '填空与简答混合'
    if subtype:
        q['special_response'] = {'version': 1, 'type': subtype, 'original_kind': q['kind'],
                                 'resolved_issues': deepcopy(q['issues']), 'answer_inferred': False}
        q['kind'] = 'response'
        q['answer'] = answer
        q['knowledge_point'] = ''
        q['issues'] = []
        q['dimensions']['response'] = 'response'


def _category(question):
    codes = {i['code'] for i in question['issues']}
    if codes & {'formula_review', 'word_script', 'formula_image_binding', 'formula_evidence'}:
        return 'formula', '公式或上下标待处理'
    if codes & {'group_material_binding', 'group_answer_binding', 'group_analysis_binding', 'duplicate_number', 'answer_marker_count'}:
        return 'ownership', '题目或材料归属待处理'
    if 'blank_answer_alignment' in codes:
        return 'blank_structure', '图内空位或混合小问待处理'
    if codes & {'unsupported_kind', 'unknown_kind'}:
        return 'nonstandard_response', '特殊作答方式待处理'
    if codes & {'image_missing', 'image_format', 'resource_integrity'}:
        return 'resource', '图片或资源待处理'
    return 'other', '其他结构待处理'


def build_special_queue(task, report):
    from .core import _split_fields
    records = []
    if report.get('document_issues'):
        return {'schema_version': 1, 'status': 'blocked_document', 'questions': [], 'total': 0}
    for q in report['questions']:
        if not q['issues'] or any(i.get('policy_skip') for i in q['issues']):
            continue
        category, label = _category(q)
        raw_fields, _ = _split_fields(q['raw_text'], q['kind'])
        records.append({'id': q['id'], 'number': q['number'], 'source': deepcopy(task['source']),
                        'status': 'pending_postprocessing', 'category': category, 'category_label': label,
                        'original_kind': q['kind'], 'raw_fields': raw_fields,
                        'question': deepcopy(q), 'automatic_answer_inference': False})
    return {'schema_version': 1, 'parser': 'special-question-handoff-1',
            'status': 'pending_postprocessing' if records else 'empty',
            'source': deepcopy(task['source']), 'total': len(records),
            'categories': dict(Counter(q['category'] for q in records)), 'questions': records}


def write_special_queue(task, report, task_dir: Path, out: Path):
    from .core import safe_file
    queue = build_special_queue(task, report)
    if not queue['total']:
        return None
    owned = {i for row in queue['questions'] for i in row['question']['block_indices']}
    queue['blocks'] = [deepcopy(b) for b in task['blocks'] if b['index'] in owned]
    refs = {token.get('id') for b in queue['blocks'] for token in b['tokens']}
    queue['formulas'] = [deepcopy(f) for f in task['formulas'] if f['id'] in refs]
    refs.update(f.get('fallback_asset_id') for f in queue['formulas'])
    queue['assets'] = [deepcopy(a) for a in task['assets'] if a['id'] in refs]
    resources = [task['source']['snapshot'], *queue['assets']]
    for formula in queue['formulas']:
        resources.append(formula['source'])
        if formula.get('preview'):
            resources.append(formula['preview'])
    path = out / '特殊题型待后处理.zip'
    if path.exists():
        raise FileExistsError(path)
    with TemporaryDirectory(prefix='.special-', dir=out) as temporary:
        pending = Path(temporary) / path.name
        with ZipFile(pending, 'x', ZIP_DEFLATED) as archive:
            archive.writestr('待后处理.json', json.dumps(queue, ensure_ascii=False, indent=2))
            archive.writestr('阅读说明.txt',
                '本包为特殊题型后处理数据，不是已通过的题库。\n'
                '题干、原始空位、答案、解析状态、问题原因及原始资源均已保留。\n'
                '解析无只表示源题没有解析，不表示题目错误。\n'
                '明确题型仍按普通流程导出；表格策略跳过另行统计。\n'
                'source/input.docx 为原始完整 Word；具体题号见待后处理.json。\n')
            seen = set()
            for resource in resources:
                if resource['path'] in seen:
                    continue
                source = safe_file(task_dir, resource['path'])
                data = source.read_bytes()
                if hashlib.sha256(data).hexdigest() != resource['sha256']:
                    raise ValueError('Special-question source resource changed')
                archive.writestr(resource['path'], data)
                seen.add(resource['path'])
        pending.rename(path)
    return {'path': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'size': path.stat().st_size, 'total': queue['total'], 'categories': queue['categories'],
            'status': 'pending_postprocessing'}
