"""Loss-aware question organization, independent of answer rendering/export.

A shared-material group is an organization, not an answer type. Numbered blanks
are slots within one question. Source block identities survive both projections.
"""
from __future__ import annotations

from collections import Counter
import re

from .config import digest

START = re.compile(r'^\s*(\d{1,5})([．、]|\.(?!\d))')
SECTION = re.compile(r'【(答案|详解|解析|分析|点睛|点精)】')
INTRO = re.compile(r'(?:完成|回答)\s*(?:下面|下列|以下)?\s*(?:各小题|小题|各题|题目|(?:相关|有关)?问题|第?\d{1,5}\s*(?:[～~—–－\-、,，]|至|和|及)\s*\d{1,5}\s*(?:小题|题))[。｡．.!！:：\s]*(?:【[^】]+】)?$')
INTRO_OBJECT = re.compile(r'⟦[IF]\d+⟧|［文档对象］')
MATERIAL_LABEL = re.compile(r'^\s*(?:【材料[一二三四五六七八九十\d]*】|(?:材料[一二三四五六七八九十\d]*|阅读下列材料)\s*[:：])')
SLOT = re.compile(r'_{2,}(\d{1,5})_{2,}')
SUBPART = re.compile(r'(?:^|\n)\s*[（(]([1-9]\d*)[）)]')
OPTION_LINE = re.compile(r'(?:^|\n|\t)\s*([A-H])[．.、]')
BLANK = re.compile(r'_{2,}|[＿﹍]{2,}|填空|填入|填写')
HEADING = re.compile(r'^\s*(?:[一二三四五六七八九十]+[、．. ]\s*)?(判断题|判断正误|简答题|论述题|作文|证明题|多选题|多项选择题|不定项选择题|单选题|单项选择题|选择题|填空题)\s*(?:[（(][^（）()\n]*[）)])?[：:]?\s*$')
UNSUPPORTED_HEADINGS = {'判断题', '判断正误', '简答题', '论述题', '作文', '证明题', '多选题', '多项选择题', '不定项选择题'}
MULTI_CHOICE_LABEL = re.compile(r'^\s*[（(【](?:多选|多项选择|不定项选择)(?:题)?[）)】]')
ANALYSIS_LIST_LEAD = re.compile(r'(?:步骤|方法|过程|如下|包括|说明|要点|原因|特点|注意事项)[^：:\n]{0,20}[：:]\s*$')


def _issue(code, message, location=''):
    return {'code': code, 'message': message, 'location': location}


def _choice_fields(raw):
    from .core import _split_fields, OPTION
    fields, problems = _split_fields(raw, 'choice')
    if not any(p['code'] == 'option_structure' for p in problems):
        return fields, problems
    question_text = SECTION.split(raw, maxsplit=1)[0]
    matches = list(OPTION.finditer(question_text))
    candidates = []
    for start, first in enumerate(matches):
        if first.group(1) != 'A' or question_text[question_text.rfind('\n', 0, first.start())+1:first.start()].strip():
            continue
        same = [m for m in matches[start:] if m.group(0)[-1] == first.group(0)[-1]]
        if [m.group(1) for m in same] == list('ABC'):
            candidates.append(same)
    if len(candidates) != 1:
        return fields, problems
    options = candidates[0]
    fields['stem'] = question_text[:options[0].start()].strip()
    fields['options'] = {m.group(1): question_text[m.end():options[i+1].start() if i+1 < len(options) else len(question_text)].strip()
                         for i, m in enumerate(options)}
    problems = [p for p in problems if p['code'] != 'option_structure']
    if fields['answer'] not in fields['options'] and not any(p['code'] == 'choice_answer' for p in problems):
        problems.append(_issue('choice_answer', '答案不是现有选项标签'))
    return fields, problems


def infer_kind(question: dict) -> tuple[str, list[str]]:
    """Evidence is per question; neither filename nor requested mode decides it."""
    from .core import _split_fields
    if MULTI_CHOICE_LABEL.match(question['stem']):
        return 'unsupported', ['题目明确标注多选或不定项选择，不能按单选题模板导出']
    fields, problems = _choice_fields(question['raw_text'])
    complete = not any(p['code'] == 'option_structure' for p in problems)
    stem = question['stem']
    option_signal = len(set(OPTION_LINE.findall(stem))) >= 2
    answer = question['answer'].strip()
    has_answer = bool(answer) and not any(p['code'] == 'answer_marker_count' for p in question['issues'])
    fill = bool(BLANK.search(stem))
    multipart = len(set(SUBPART.findall(stem)) & set(SUBPART.findall(answer))) >= 2
    slots = len(re.findall(r'_{2,}|[＿﹍]{2,}', stem))
    answer_fields = [p for p in re.split(r'\s{2,}|\n', answer) if p.strip()]
    letter_hint = re.fullmatch(r'(?:答案(?:为|是|[:：])?|应?选(?:择)?|故选)?[A-H](?:项|选项)?[。.]?', re.sub(r'\s+', '', answer))
    descriptive = bool(answer) and not re.fullmatch(r'[A-H\s、,，.．;；]+', answer)
    multi = slots >= 2 and descriptive and not letter_hint and (not complete or len(answer_fields) >= 2)
    if (multipart or multi) and fill and has_answer:
        return 'blank', ['多个空位或编号小问与独立填入答案相对应']
    if complete:
        return 'choice', ['正文有按顺序排列的连续选项标签']
    if fill and not option_signal and has_answer:
        return 'blank', ['题干有填空位置或明确填入指令及独立答案']
    if not fill and not option_signal and answer:
        return 'unsupported', ['有独立答案但无选择选项或填空位置，作答方式暂未支持']
    return 'unknown', ['题型或答案归属证据不足，需复核']


def _decorate(q: dict, blocks: list[dict], forced_kind=None):
    from .core import _split_fields
    q['parent_id'] = None
    q['number_path'] = [q['number']]
    q['material_refs'] = []
    q['source_spans'] = [{'block_index': b['index'], 'location': b.get('location', '')}
                         for b in blocks if b['index'] in q['block_indices']]
    q['organization'] = 'subquestions' if SUBPART.search(q['stem']) else 'independent'
    kind, evidence = infer_kind(q)
    if forced_kind:
        kind, evidence = 'unsupported', [f'正文分节标注为{forced_kind}，当前不支持该作答方式']
    if kind == 'choice':
        fields, issues = _choice_fields(q['raw_text'])
        q.update(fields)
        q['issues'] = [p for p in q['issues'] if p['code'] != 'answer_marker_count'] + issues
        q['option_labels'] = list(q['options'])
    elif kind in ('unsupported', 'unknown'):
        if len(set(re.findall(r'(?<![A-Za-z])([A-H])[．.、]', q['stem']))) >= 2:
            _, choice_problems = _choice_fields(q['raw_text'])
            q['issues'].extend(p for p in choice_problems if p['code'] == 'option_structure')
        q['issues'].append(_issue('unsupported_kind' if kind == 'unsupported' else 'unknown_kind', evidence[0], q['location']))
    q['kind'] = kind
    q['classification_evidence'] = evidence
    q['dimensions'] = {'organization': q['organization'], 'response': kind,
                       'carrier': 'table' if any(b.get('kind') == 'table_row' and b['index'] in q['block_indices'] for b in blocks) else 'body'}
    return q


def _legacy(blocks):
    from .core import _questions
    questions, preamble = _questions(blocks, 'blank', {})
    heading = None
    headings = []
    for b in blocks:
        match = HEADING.fullmatch(b['text'])
        if match:
            headings.append((b['index'], match.group(1)))
    for q in questions:
        for index, title in headings:
            if index <= q['block_indices'][0]:
                heading = title
        unsupported = heading if heading in UNSUPPORTED_HEADINGS else None
        _decorate(q, blocks, unsupported)
    return questions, preamble


def _material_intro(b):
    # Inline drawings follow the instruction in many Word paragraphs. They
    # must not hide the boundary, either in classification's plain projection
    # or the parser's asset/formula projection. Only normalize for detection:
    # the original paragraph (including every object) remains shared material.
    text = INTRO_OBJECT.sub('', b['text'])
    return not START.match(text) and not SECTION.search(text) and bool(INTRO.search(text))


def _material_starts(blocks):
    """An explicit material label can establish a multi-paragraph start.

    Unmarked prose preceding an instruction is not reassigned by proximity.
    """
    starts = set()
    for index, block in enumerate(blocks):
        if not _material_intro(block):
            continue
        start = index
        for prior in range(index - 1, -1, -1):
            text = blocks[prior]['text']
            if START.match(text) or SECTION.search(text) or HEADING.fullmatch(text) or _material_intro(blocks[prior]):
                break
            if MATERIAL_LABEL.match(text):
                start = prior
                break
        starts.add(start)
    return starts


def _numbered_parts(text, allowed, inline=False):
    pattern = r'(?<!\d)(\d{1,5})(?:[．、]|\.(?!\d))' if inline else r'(?:^|\n)\s*(\d{1,5})(?:[．、]|\.(?!\d))'
    matches = list(re.finditer(pattern, text))
    if not matches or text[:matches[0].start()].strip():
        return None
    numbers = [m.group(1) for m in matches]
    if len(numbers) != len(set(numbers)) or set(numbers) - set(allowed):
        return None
    return {m.group(1): text[m.end():matches[i+1].start() if i+1 < len(matches) else len(text)].strip()
            for i, m in enumerate(matches)}


def _shared(blocks, start, first_answer, starts, end):
    """Project explicitly numbered group answers onto their own child nodes."""
    from .core import _split_fields
    numbers = [m.group(1) for _, m in starts]
    material = blocks[start:starts[0][0]]
    group_location = blocks[start].get('location', '')
    gid = 'g-' + digest((group_location + ':' + ','.join(numbers)).encode())[:16]
    material_text = '\n'.join(b['text'] for b in material).strip()
    tail = '\n'.join(b['text'] for b in blocks[first_answer:end]).strip()
    sections = list(SECTION.finditer(tail))
    answers, analyses, shared_analysis = {}, {n: [] for n in numbers}, []
    problems = []
    answer_sections = 0
    for i, match in enumerate(sections):
        body = tail[match.end():sections[i+1].start() if i+1 < len(sections) else len(tail)].strip()
        if match.group(1) in ('点睛', '点精'):
            continue
        if match.group(1) == '答案':
            answer_sections += 1
            parsed = _numbered_parts(body, numbers, inline=True)
            if parsed is None:
                problems.append(_issue('group_answer_binding', '集中答案题号与子题不一一对应，未猜测答案归属', group_location))
            else:
                answers = parsed
        else:
            parsed = _numbered_parts(body, numbers)
            # A common analysis paragraph may precede numbered child analyses.
            # Do not copy that entire section into every child. Explicit common
            # step lists keep their established meaning instead.
            numbered_start = re.search(r'(?:^|\n)\s*(\d{1,5})(?:[．、]|\.(?!\d))', body)
            if (parsed is None and match.group(1) == '分析' and numbered_start
                    and numbered_start[1] in numbers and numbered_start.start() > 0
                    and not ANALYSIS_LIST_LEAD.search(body[:numbered_start.start()])):
                parsed = _numbered_parts(body[numbered_start.start():].strip(), numbers)
                if parsed:
                    shared_analysis.append(match.group(0) + body[:numbered_start.start()].strip())
                else:
                    problems.append(_issue('group_analysis_binding', '公共分析后的编号解析无法唯一关联，未整段复制给子题', group_location))
                    continue
            if parsed:
                for n, content in parsed.items():
                    analyses[n].append(match.group(0) + content)
            elif match.group(1) == '分析' and START.match(body):
                problems.append(_issue('group_analysis_binding', '编号分析无法与子题一一对应，未作为公共解析复制', group_location))
            elif match.group(1) == '分析':
                shared_analysis.append(match.group(0) + body)
            elif body:
                problems.append(_issue('group_analysis_binding', '集中解析缺少可验证的子题编号，未猜测归属', group_location))
    if answer_sections != 1:
        problems.append(_issue('group_answer_binding', '题组必须有一个可定位的集中答案区域', group_location))
    qs = []
    answer_indices = [b['index'] for b in blocks[first_answer:end]]
    for i, (pos, match) in enumerate(starts):
        n = match.group(1)
        own = blocks[pos:starts[i+1][0] if i+1 < len(starts) else first_answer]
        content = '\n'.join([own[0]['text'][match.end():], *(b['text'] for b in own[1:])]).strip()
        stem_raw = '\n'.join(v for v in (material_text, content) if v)
        analysis = '\n'.join([*analyses[n], *shared_analysis])
        raw = stem_raw + '\n【答案】' + answers.get(n, '') + ('\n' + analysis if analysis else '')
        fields, issues = _split_fields(raw, 'blank')
        indices = [b['index'] for b in material + own] + answer_indices
        q = {'id': 'q' + n + '-' + digest((blocks[pos].get('location', '') + ':' + n).encode())[:12],
             'number': n, 'raw_text': raw, 'block_indices': list(dict.fromkeys(indices)),
             'location': blocks[pos].get('location', ''), 'issues': [*issues, *problems], **fields}
        if n not in answers and not problems:
            q['issues'].append(_issue('group_answer_binding', '集中答案中缺少该子题编号，未借用其他子题答案', q['location']))
        _decorate(q, blocks)
        q.update(parent_id=gid, organization='shared_material', number_path=[gid, n],
                 material_refs=[b['index'] for b in material],
                 answer_source_spans=[{'block_index': b['index'], 'location': b.get('location', '')} for b in blocks[first_answer:end]])
        q['dimensions']['organization'] = 'shared_material'
        for b in material + own + blocks[first_answer:end]:
            for token in b.get('tokens', []):
                if token.get('kind') == 'unsupported':
                    q['issues'].append(_issue('unsupported_content', token.get('reason', 'Unsupported source content'), token.get('location', '')))
        qs.append(q)
    group = {'id': gid, 'location': group_location, 'material_block_indices': [b['index'] for b in material],
             'child_ids': [q['id'] for q in qs], 'numbers': numbers,
             'source_block_indices': [b['index'] for b in blocks[start:end]]}
    return qs, group


def _new_question_at(blocks, start):
    """A repeated number needs its own response body and answer evidence.

    Numbered analysis prose alone is never a new question. A later independent
    answer and options/blanks establish a second physical question, which the
    final duplicate-number validation will quarantine rather than lose.
    """
    body = [blocks[start]['text']]
    for block in blocks[start+1:]:
        text = block['text']
        if START.match(text) or _material_intro(block) or HEADING.fullmatch(text):
            return False
        section = SECTION.search(text)
        if section:
            if section.group(1) != '答案':
                return False
            raw = '\n'.join(body + [text])
            _, issues = _choice_fields(raw)
            return (not any(p['code'] == 'option_structure' for p in issues)
                    or bool(BLANK.search('\n'.join(body))))
        body.append(text)
    return False


def _has_own_answer_at(blocks, start):
    """A tip list number alone is not a question; retain even unsupported
    response types when a separate answer proves a genuine next question."""
    for index in range(start, len(blocks)):
        text = blocks[index]['text']
        if index > start and (START.match(text) or _material_intro(blocks[index]) or HEADING.fullmatch(text)):
            return False
        section = SECTION.search(text)
        if section:
            return section.group(1) == '答案'
    return False


def _has_response_body_at(blocks, start):
    """Visible options/blank slots protect incomplete questions from absorption."""
    body = []
    for index in range(start, len(blocks)):
        text = blocks[index]['text']
        if index > start and (START.match(text) or _material_intro(blocks[index]) or HEADING.fullmatch(text)):
            break
        section = SECTION.search(text)
        body.append(text[:section.start()] if section else text)
        if section:
            break
    text = '\n'.join(body)
    return (len(set(re.findall(r'(?<!\S)([A-H])[．.、]', text))) >= 2
            or bool(re.search(r'_{2,}|[＿﹍]{2,}', text)))


def _explanation_list_positions(blocks):
    """Explicit analysis lead-in + ordered list, with no independent answers.

    Do not relax duplicate-number validation: complete repeated questions and
    unintroduced/malformed boundaries retain their existing review behavior.
    """
    positions = set()
    section, previous = None, ''
    for index, block in enumerate(blocks):
        text = block['text']
        markers = list(SECTION.finditer(text))
        if markers:
            section = markers[-1].group(1)
        start = START.match(text)
        if (start and start[1] == '1' and section in ('分析', '详解', '解析')
                and ANALYSIS_LIST_LEAD.search(previous)):
            candidates, expected = [], 1
            for offset in range(index, len(blocks)):
                value = blocks[offset]['text']
                if SECTION.search(value) or HEADING.fullmatch(value) or _material_intro(blocks[offset]):
                    break
                number = START.match(value)
                if number:
                    if (int(number[1]) != expected or _has_own_answer_at(blocks, offset)
                            or _has_response_body_at(blocks, offset)):
                        break
                    candidates.append(offset)
                    expected += 1
            if len(candidates) >= 2:
                positions.update(candidates)
        if start and index not in positions and not markers:
            section = None
        if text.strip():
            previous = text
    return positions


def _numbered_blank_ranges(blocks):
    """Only split unnumbered slot groups proven by an exact numbered answer key."""
    ranges = []
    for answer_index, block in enumerate(blocks):
        if not block['text'].lstrip().startswith('【答案】'):
            continue
        previous_answers = [i for i in range(answer_index) if '【答案】' in blocks[i]['text']]
        lower = previous_answers[-1] + 1 if previous_answers else 0
        candidates = [(i, SLOT.findall(blocks[i]['text'])) for i in range(lower, answer_index) if SLOT.search(blocks[i]['text'])]
        if not candidates:
            continue
        # A genuinely numbered parent already gives this content its boundary.
        if any(START.match(blocks[i]['text']) for i in range(lower, candidates[0][0] + 1)):
            continue
        slots = [n for _, ns in candidates for n in ns]
        parsed = _numbered_parts(block['text'].split('【答案】', 1)[1].strip(), slots, inline=True)
        if parsed is None or set(parsed) != set(slots) or len(slots) != len(set(slots)):
            continue
        # Previous answers may continue on explicitly numbered subpart lines.
        start = lower
        while start < candidates[0][0] and (not blocks[start]['text'].strip() or SUBPART.match(blocks[start]['text'])):
            start += 1
        # Never include an explanation section from the preceding question.
        if any(SECTION.search(blocks[i]['text']) for i in range(start, candidates[0][0])):
            continue
        ranges.append((start, answer_index + 1, slots))
    return ranges


def build_questions(blocks: list[dict], requested_kind: str, config: dict):
    """Return legacy-compatible rows plus explicit organization/source metadata."""
    from .core import _split_fields
    questions, preamble, groups = [], [], []
    numbered = {start: (end, slots) for start, end, slots in _numbered_blank_ranges(blocks)}
    material_starts = _material_starts(blocks)
    explanation_lists = _explanation_list_positions(blocks)
    cursor = 0
    normal = []
    def flush():
        if normal:
            qs, pre = _legacy(normal)
            questions.extend(qs)
            preamble.extend(pre)
            normal.clear()
    while cursor < len(blocks):
        if HEADING.fullmatch(blocks[cursor]['text']):
            flush()
            preamble.append(blocks[cursor])
            cursor += 1
            continue
        if cursor in numbered:
            flush()
            end, slots = numbered[cursor]
            span = blocks[cursor:end]
            raw = '\n'.join(b['text'] for b in span).strip()
            fields, issues = _split_fields(raw, 'blank')
            number = slots[0] + '–' + slots[-1]
            q = {'id': 'q' + number + '-' + digest((span[0].get('location', '') + ':' + number).encode())[:12],
                 'number': number, 'raw_text': raw, 'block_indices': [b['index'] for b in span],
                 'location': span[0].get('location', ''), 'issues': issues, **fields}
            _decorate(q, span)
            q.update(organization='numbered_blanks', numbered_blank_ids=slots)
            q['dimensions']['organization'] = 'numbered_blanks'
            questions.append(q)
            cursor = end
            continue
        block = blocks[cursor]
        material_start = cursor in material_starts
        potential = material_start or START.match(block['text'])
        if potential:
            starts, first_answer = [], None
            for j in range(cursor, len(blocks)):
                if j > cursor and (j in material_starts or j in numbered or HEADING.fullmatch(blocks[j]['text'])):
                    break
                if '【答案】' in blocks[j]['text']:
                    first_answer = j
                    break
                m = START.match(blocks[j]['text'])
                if m:
                    starts.append((j, m))
            numbers = [m.group(1) for _, m in starts]
            # A material introduction plus a numbered key also supports a
            # one-child group, while ordinary questions keep their exact text.
            numbered_key = first_answer is not None and bool(re.match(r'\s*【答案】\s*\d+[．、.]', blocks[first_answer]['text']))
            shared = first_answer is not None and starts and len(numbers) == len(set(numbers)) and (material_start and (len(starts) > 1 or numbered_key) or numbered_key and len(starts) > 1)
            if shared:
                end = first_answer + 1
                section_kind = list(SECTION.finditer(blocks[first_answer]['text']))[-1].group(1)
                while end < len(blocks):
                    m = START.match(blocks[end]['text'])
                    # Explanation lists often restart at 1 with a different
                    # separator (e.g. 1、 after questions 116．/117．). Keep
                    # those paragraphs in the group's analysis, unless their
                    # own response body and answer prove a new question.
                    new_question = m and (
                        (m.group(2) == starts[0][1].group(2) and m.group(1) not in numbers)
                        or _has_own_answer_at(blocks, end)
                        or (m.group(1) not in numbers and _has_response_body_at(blocks, end))
                    )
                    if end in explanation_lists:
                        new_question = False
                    if m and section_kind in ('点睛', '点精'):
                        new_question = _has_own_answer_at(blocks, end)
                    if end in material_starts or end in numbered or HEADING.fullmatch(blocks[end]['text']) or new_question:
                        break
                    sections = list(SECTION.finditer(blocks[end]['text']))
                    if sections:
                        section_kind = sections[-1].group(1)
                    end += 1
                flush()
                qs, group = _shared(blocks, cursor, first_answer, starts, end)
                questions.extend(qs)
                groups.append(group)
                cursor = end
                continue
        normal.append(block)
        cursor += 1
    flush()
    # Section policy applies across flushes and shared groups, not just the
    # legacy segment that happened to contain the heading.
    headings = [(b['index'], HEADING.fullmatch(b['text']).group(1))
                for b in blocks if HEADING.fullmatch(b['text'])]
    for q in questions:
        heading = next((title for index, title in reversed(headings)
                        if index <= min(q['block_indices'])), None)
        if heading in UNSUPPORTED_HEADINGS:
            q['kind'] = q['dimensions']['response'] = 'unsupported'
            message = f'正文分节标注为{heading}，当前不支持该作答方式'
            q['classification_evidence'] = [message]
            if not any(i['code'] == 'unsupported_kind' for i in q['issues']):
                q['issues'].append(_issue('unsupported_kind', message, q['location']))
    _guard_unbound_material(blocks, questions, groups, material_starts)
    counts = Counter(q['number'] for q in questions)
    for q in questions:
        if counts[q['number']] > 1 and not any(p['code'] == 'duplicate_number' for p in q['issues']):
            q['issues'].append(_issue('duplicate_number', 'Duplicate source question number', q['location']))
    children = sum(len(g['child_ids']) for g in groups)
    return questions, preamble, {'version': 1, 'groups': groups,
        'original_question_count': len(questions) - children + len(groups),
        'subquestion_count': children, 'output_candidate_count': len(questions)}


def _guard_unbound_material(blocks, questions, groups, starts):
    """Do not silently export a group whose material scope was not established.

    A directive inside a numbered prompt is ordinary question content. An
    unowned directive or one swallowed by an earlier answer/analysis is not.
    Until the next material/section boundary its possible consumers require
    review; proximity alone must never choose which questions inherit it.
    """
    bound = {index for group in groups for index in group['material_block_indices']}
    first_sections = {q['id']: next((b['index'] for b in blocks
                                    if b['index'] in q['block_indices'] and SECTION.search(b['text'])), float('inf'))
                      for q in questions}
    for start in sorted(starts):
        material = blocks[start]
        if material['index'] in bound:
            continue
        owners = [q for q in questions if material['index'] in q['block_indices']]
        if any(material['index'] < first_sections[q['id']] for q in owners):
            continue
        end = next((i for i in range(start + 1, len(blocks))
                    if i in starts or HEADING.fullmatch(blocks[i]['text'])), len(blocks))
        scope = {b['index'] for b in blocks[start:end]}
        for q in questions:
            if q in owners or scope.intersection(q['block_indices']):
                q['issues'].append(_issue('group_material_binding',
                    '共享材料未建立可靠的子题范围，需核对逐题答案或题组边界后再导出', material.get('location', '')))
