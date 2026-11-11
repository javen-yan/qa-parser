"""Evidence-based, read-only explanations for questions excluded from export.

The view never repairs questions, accepts guessed formula text, or invokes a
renderer for rejected WMF. Only bounded, hash-checked raster previews are shown.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import html
import io
import json
from pathlib import Path
import re

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 12_000_000
MAX_EMBEDDED_BYTES = 12 * 1024 * 1024
REF = re.compile(r'⟦([FI]\d{6})⟧')
LABELS = {'stem': '题干', 'answer': '答案', 'analysis': '解析', 'question': '整题（无法定位到单一字段）'}
CATEGORIES = {'converter': '转换器暂不支持', 'fidelity': '图片字体保真限制',
              'structure': '内容关联或结构限制', 'resource': '文档或资源不可用'}


def _label(key: str) -> str:
    return ('选项 ' + key.split('.', 1)[1]) if key.startswith('options.') else LABELS.get(key, key)


def _location_label(location: str) -> str:
    parts = []
    for pattern, label in [(r'/w:tbl\[(\d+)\]', '表格'), (r'/w:tr\[(\d+)\]', '行'),
                           (r'/w:tc\[(\d+)\]', '列'), (r'/w:p\[(\d+)\]', '段落'),
                           (r'/w:r\[(\d+)\]', '文本片段')]:
        match = re.search(pattern, location or '')
        if match:
            parts.append(f'{label} {match.group(1)}')
    return 'Word 结构位置：' + ' · '.join(parts) if parts else '源位置未能可靠定位'


def _description(issue: dict, formula: dict | None) -> tuple[str, str, str, str]:
    """category, short title, evidence-based explanation, consequence."""
    code, message = issue.get('code', ''), issue.get('message', '')
    reason = (formula or {}).get('reason', message)
    fallback = (formula or {}).get('fallback_error', '')
    if code == 'word_script' or 'Word script has no reliably identifiable adjacent base' in reason:
        return ('structure', '上下标无法确定归属',
                'Word 将这里的上标或下标单独保存，当前转换器无法可靠确定它应附在哪个相邻内容上；源内容不一定有错。',
                '直接拼接可能改变公式含义，因此保留前后文字并标记此位置，整题未导出。')
    if code == 'formula_review':
        details = []
        if '字体私有区字符' in reason:
            details.append('公式使用字体专用字符编码，当前转换器无法可靠确定这些编码对应的符号。')
        if '自定义字体字符' in reason:
            details.append('公式含自定义字体字符，尚未建立可靠的符号对应关系。')
        if '多行/矩阵' in reason:
            details.append('公式含多行或矩阵排列，当前转换器尚不能保证行列与内容对应正确。')
        if '装饰' in reason:
            details.append('公式含尚未支持的装饰标记，直接忽略会改变原有表达。')
        if '模板' in reason:
            details.append('公式使用尚未适配的内部排版模板，无法可靠转换成可导入的公式。')
        if '扩展记录' in reason:
            details.append('公式含当前转换器尚不识别的扩展内容，不能确认跳过它是否会改变含义。')
        if not details:
            details.append('当前转换器尚不能可靠处理这个公式对象；这并不表示题目或答案有错。')
        category, title = 'converter', '公式内容暂不能可靠转换'
        font = re.search(r'unverified font for text: (.+)', fallback)
        if font:
            category, title = 'fidelity', '公式原图的字体无法可靠显示'
            name = font.group(1).strip()
            if name.startswith('non-ASCII font bytes'):
                details.append('原始公式预览使用非拉丁编码的字体名称，现有图片转换器尚未验证该字体的字形对应。')
            else:
                details.append(f'在原始公式预览中发现“{name}”字体，现有图片转换器尚不能保证这种字体的字形完整；预览还可能包含其他字体。')
            details.append('未采用可能漏字的转换图片；证据不足以断定具体哪个符号会缺失。')
        elif 'font charset' in fallback or 'non-ASCII glyph mapping' in fallback:
            category, title = 'fidelity', '公式原图的字符编码未验证'
            details.append('原始预览中的字体字符编码尚未验证，直接转换可能把字符映射成错误字形或丢失字形，无法确定具体受影响的符号。')
        elif 'not available' in fallback:
            details.append('当前运行环境缺少原始公式图片所需的离线转换工具。')
        elif 'No reliable original preview' in fallback or not (formula or {}).get('preview'):
            details.append('该对象没有可可靠关联的原始预览图，无法用图片替代。')
        elif fallback:
            details.append('原始公式图片也未通过完整性或显示保真检查，因此未用图片替代。')
        if '多行/矩阵' in reason and 'alignment=' in reason:
            category, title = 'converter', '公式排版暂不支持'
            details.insert(0, '源对象中的行列内容仍然存在，但其对齐参数超出当前已确认的规则；直接按默认居中处理可能改变条件的位置。')
        return category, title, ''.join(details), '此处公式不能完整保留，可能影响题意、选项或解答，因此整题未导出。'
    descriptions = {
        'table_interaction_skipped': ('structure', '按策略跳过表格交互填空', '这道题要求在表格单元格中填写内容；当前策略保留源表格和位置证据，不生成可能错位的答案。', '仅此题未导出，其他无依赖题目继续处理。'),
        'table_role_unknown': ('structure', '表格用途需要复核', '无法可靠区分此表格是材料还是作答区域。', '未猜测或丢弃表格，保留整题供复核。'),
        'blank_answer_alignment': ('structure', '空位与答案无法一一对应', '文字空位、小问编号与答案字段不能完整对应，或含有无文字空位的图片/开放作答。', '未生成猜测的考点或答案序列，整题未导出。'),
        'group_answer_binding': ('structure', '子题答案归属不明确', '集中答案中的题号缺失、重复或与子题不匹配。', '不能将答案猜配给子题，受影响子题未导出。'),
        'group_analysis_binding': ('structure', '子题解析归属不明确', '集中解析缺少可验证的子题编号。', '受影响子题保留源内容供复核。'),
        'unknown_kind': ('structure', '作答方式不明确', '正文证据不足以可靠识别选择或填空结构。', '此题未导出，不影响其他明确题型。'),
        'unsupported_kind': ('structure', '暂未支持的作答方式', '此题属于简答、绘图等当前未支持的作答方式，未强制转换为填空题。', '保留原题与答案供复核，其他题继续处理。'),
        'formula_image_binding': ('resource', '公式与预览图无法确认对应', '公式对象与图片之间的对应证据缺失或不一致，不能确认这张图属于此处公式。', '为避免把别处公式放进本题，整题未导出。'),
        'formula_evidence': ('resource', '公式原始资源缺失或变化', '保存的公式证据无法读取，或内容与解析时记录不一致。', '当前无法验证公式内容，整题未导出。'),
        'image_missing': ('resource', '图片资源缺失或变化', '此处引用的图片无法读取，或与解析时保存的内容不一致。', '无法完整呈现题目图片，整题未导出。'),
        'image_format': ('converter', '图片格式或变换暂不支持', '当前工具不能可靠保留此图片的格式或裁剪等显示变换。未将未经验证的图片当作原文。', '图示可能影响题意，整题未导出。'),
        'answer_marker_count': ('structure', '答案无法唯一归属', '题目范围内未找到唯一的答案标记；可能是文档排版或分段方式超出当前规则。', '不能确定哪段内容是本题答案，因此整题未导出，未判断原文答案对错。'),
        'choice_answer': ('structure', '答案与单选格式不匹配', '提取到的答案不是单独的 A、B、C 或 D，当前单选导出规则无法确定选项对应。', '保留原有答案供查看，整题未导出，不擅自修改答案。'),
        'option_structure': ('structure', '选项边界无法可靠识别', '没有识别到唯一且顺序完整的 A、B、C、D 选项组，文字或表格分隔可能超出当前规则。', '部分选项可能无法准确分栏，因此整题未导出。'),
        'empty_option': ('structure', '某个选项没有提取到内容', '该选项为空或未能从源文档中可靠提取，不能据此判断原文确实没有选项。', '选择题选项不完整，整题未导出。'),
        'required_field': ('structure', '必要字段没有提取到内容', '当前提取结果缺少模板要求的必要字段，可能是内容缺失或文档排版尚不支持。', '未生成不完整题库行。'),
        'duplicate_number': ('structure', '源题号重复', '同一份文档中识别到重复题号，无法可靠建立题目与答案的唯一对应。', '相关题目未导出，保留原题号供定位。'),
        'resource_integrity': ('resource', '文档资源无法校验', '源快照、图片或公式资源缺失或内容已改变，当前任务的完整性检查未通过。', '相关文档未被当作可靠导出结果。'),
        'source_changed': ('resource', '源文档已改变', '当前源文件与建立任务时的版本不同，旧解析结果不能直接套用。', '需要按当前文件重新转换，原任务结果不作成功导出。'),
        'run_failure': ('resource', '文档无法读取或处理', '未能完成读取或处理此文档，无法形成可靠的逐题内容。', '没有生成成功题库文件；可检查原始 Word 是否可正常打开。'),
        'no_questions': ('structure', '未识别到题目边界', '当前编号识别规则未找到可可靠拆分的题目。', '无法确定逐题内容与题号，不生成空成功题库。'),
        'export_content': ('converter', '内容无法完整写入 Excel', '此题内容未通过 Excel 字段写入检查，当前工具不会静默截断内容。', '该题没有写入最终 Excel。'),
        'kind_mismatch': ('structure', '所选题型与文档不一致', '选择的处理方式与文档中的题型线索不一致。', '未强行套用另一种模板。'),
        'unsupported_kind': ('converter', '题型尚不支持', '文档包含超出当前单选或填空处理范围的题型。', '未强行按单选或填空导出。'),
    }
    return descriptions.get(code, ('converter', '部分内容尚不能可靠处理',
                                   '此处文档内容超出当前转换规则，保留能读取的文字供定位；不是对题目正确性的判断。',
                                   '为避免遗漏或错位，相关题目未导出。'))


class _Images:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.budget = MAX_EMBEDDED_BYTES
        self.cache = {}

    def load(self, resource: dict | None) -> tuple[str | None, str]:
        if not resource:
            return None, '没有可用的原始图片资源。'
        key = (resource.get('path'), resource.get('sha256'))
        if key in self.cache:
            cached = self.cache[key]
            if cached[0]:
                size = len(cached[0])
                if size > self.budget:
                    return None, '本文件预览图片已达到大小限制；其余图片请查看源 Word。'
                self.budget -= size
            return cached
        try:
            name = resource['path']
            relative = Path(name)
            if relative.is_absolute() or '..' in relative.parts or '\\' in name:
                raise ValueError('unsafe resource path')
            path = (self.root / relative).resolve()
            if not path.is_relative_to(self.root) or not path.is_file():
                raise ValueError('missing resource')
            if path.stat().st_size > MAX_IMAGE_BYTES:
                return None, '图片较大，未在问题预览中加载；请查看源 Word。'
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != resource['sha256']:
                raise ValueError('resource hash mismatch')
            from PIL import Image
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in {'PNG', 'JPEG', 'GIF', 'BMP', 'TIFF', 'WEBP'}:
                    return None, '图片格式暂不支持。'
                if image.width * image.height > MAX_PIXELS or getattr(image, 'n_frames', 1) != 1:
                    return None, '图片尺寸或多帧结构超出预览范围；请查看源 Word。'
                image.load()
                raster = image.convert('RGBA')
                raster.thumbnail((1200, 1000))
                output = io.BytesIO()
                raster.save(output, format='PNG')
            encoded = base64.b64encode(output.getvalue()).decode('ascii')
            if len(encoded) > self.budget:
                return None, '本文件预览图片已达到大小限制；其余图片请查看源 Word。'
            self.budget -= len(encoded)
            result = ('data:image/png;base64,' + encoded, '')
        except Exception:
            result = (None, '图片资源缺失、变化或无法安全读取；请查看源 Word。')
        self.cache[key] = result
        return result


def build_problem_preview(task_dir: Path, source_name: str = '') -> dict:
    """Return an explanation-only view of excluded questions; never mutate task."""
    from .core import load_task, validate_task, _split_fields
    result = {'schema_version': 1, 'source_name': source_name, 'kind': '', 'kind_label': '',
              'notice': '以下题目未导出。',
              'empty_message': '此文件没有未导出的题目。', 'document_issues': [], 'questions': []}
    try:
        task, root = load_task(Path(task_dir))
        report = validate_task(task, root)
    except Exception as exc:
        result['empty_message'] = '无法读取可靠的题目预览，请打开源 Word 查看原文。'
        result['document_issues'] = [{'id': 'document-1', 'code': 'run_failure', 'category': 'resource',
            'category_label': CATEGORIES['resource'], 'label': '题目预览不可用',
            'reason': '任务或文档证据缺失、变化或无法读取，不能显示未经验证的题目内容。',
            'impact': '没有将此预览当作成功转换。', 'field': 'question', 'field_label': _label('question'),
            'source': '', 'source_label': '源位置不可用', 'diagnostic': f'{type(exc).__name__}: {exc}'}]
        return result
    result.update(source_name=source_name or task['source']['name'], kind=task['kind'],
                  kind_label={'choice': '选择题', 'blank': '填空 / 考点', 'mixed': '混合题型'}.get(task['kind'], '待复核题型'))
    formulas = {f['id']: f for f in task['formulas']}
    assets = {a['id']: a for a in task['assets']}
    images = _Images(root)
    global_issues = report.get('document_issues', [])
    for number, entry in enumerate(global_issues, 1):
        cat, title, reason, impact = _description(entry, None)
        result['document_issues'].append({'id': f'document-{number}', 'code': entry['code'],
            'category': cat, 'category_label': CATEGORIES[cat], 'label': title, 'reason': reason,
            'impact': impact, 'field': 'question', 'field_label': _label('question'),
            'source': entry.get('location', ''), 'source_label': _location_label(entry.get('location', '')),
            'diagnostic': entry.get('message', '')})
    for original in report['questions']:
        issues = deepcopy(original.get('issues', [])) + deepcopy(global_issues)
        if not issues:
            continue
        question_kind = original.get('kind', task['kind'])
        raw_fields, _ = _split_fields(original['raw_text'], question_kind)
        values = [('stem', raw_fields['stem'])] + [('options.' + k, v) for k, v in raw_fields['options'].items()]
        values += [('answer', raw_fields['answer']), ('analysis', raw_fields['analysis'])]
        ref_fields = {ref: key for key, text in values for ref in REF.findall(text)}
        loc_refs = {f.get('location'): fid for fid, f in formulas.items() if fid in original.get('formula_ids', [])}
        loc_refs.update({a.get('location'): aid for aid, a in assets.items() if aid in REF.findall(original['raw_text'])})
        question = {k: original[k] for k in ('id', 'number', 'location')}
        question.update(kind=question_kind, kind_label={'choice': '选择题', 'blank': '填空 / 考点'}.get(question_kind, '待复核题型'), fields=[], problems=[],
                        location_label=_location_label(original['location']), preview_notes=[])
        refs_to_problems, fields_to_problems, seen = {}, {}, {}
        for entry in issues:
            fid = entry.get('formula_id') or loc_refs.get(entry.get('location'))
            formula = formulas.get(fid)
            code = entry.get('code', '')
            key = ref_fields.get(fid, 'question')
            if key == 'question':
                if code in {'answer_marker_count', 'choice_answer'}:
                    key = 'answer'
                elif code == 'empty_option':
                    match = re.search(r'option ([A-H])', entry.get('message', ''))
                    if match: key = 'options.' + match.group(1)
                elif code == 'required_field':
                    match = re.search(r'field is empty: (\w+)', entry.get('message', ''))
                    if match and match.group(1) in LABELS: key = match.group(1)
            cat, title, reason, impact = _description(entry, formula)
            signature = (fid, key, reason, entry.get('location', ''))
            if signature in seen:
                prior = seen[signature]
                prior['diagnostic'] += '\n' + code + ': ' + entry.get('message', '')
                continue
            pid = f"{original['id']}-problem-{len(question['problems'])+1}"
            problem = {'id': pid, 'code': code, 'category': cat, 'category_label': CATEGORIES[cat],
                       'label': title, 'reason': reason, 'impact': impact, 'field': key, 'field_label': _label(key),
                       'source': entry.get('location', ''), 'source_label': _location_label(entry.get('location', '')),
                       'diagnostic': code + ': ' + entry.get('message', '')}
            seen[signature] = problem
            question['problems'].append(problem)
            fields_to_problems.setdefault(key, []).append(pid)
            if fid: refs_to_problems.setdefault(fid, []).append(pid)
        for key, text in values:
            field = {'key': key, 'label': _label(key), 'segments': [], 'problem_ids': fields_to_problems.get(key, [])}
            cursor = 0
            for match in REF.finditer(text):
                if match.start() > cursor:
                    field['segments'].append({'type': 'text', 'text': text[cursor:match.start()], 'problem_ids': []})
                ref = match.group(1)
                segment = {'ref_id': ref, 'problem_ids': refs_to_problems.get(ref, [])}
                formula, asset = formulas.get(ref), assets.get(ref)
                if formula:
                    segment['source'] = formula.get('location', '')
                    if formula['status'] == 'converted' and formula.get('latex') and not segment['problem_ids']:
                        segment.update(type='latex', latex=formula['latex'], text='$$' + formula['latex'] + '$$')
                    elif formula['status'] == 'image' and not segment['problem_ids']:
                        asset = assets.get(formula.get('fallback_asset_id'))
                        src, error = images.load(asset)
                        segment.update(type='image', src=src, alt='已通过检查的原始公式预览（缩略图）') if src else segment.update(type='placeholder', text=error)
                    else:
                        # A rejected WMF is never rendered here, even if the
                        # converter previously returned a partial image.
                        preview = formula.get('preview')
                        binding = formula.get('preview_binding') or {}
                        src, error = images.load(preview) if binding.get('verified') else (None, '没有可可靠关联的原始预览图。')
                        if src:
                            segment.update(type='image', src=src, alt='原文附带预览图（仅供定位，此公式未成功转换）')
                        else:
                            script_fragment = re.fullmatch(r'\{\}([_^])\{([0-9]+)\}', formula.get('latex') or '') if formula.get('format') == 'latex' else None
                            if script_fragment:
                                label = '下标' if script_fragment.group(1) == '_' else '上标'
                                segment.update(type='placeholder', text=f'【原文独立{label}“{script_fragment.group(2)}”：归属无法确认】' + error)
                            else:
                                segment.update(type='placeholder', text='【公式未能保留】' + error)
                elif asset:
                    segment['source'] = asset.get('location', '')
                    src, error = images.load(asset) if not asset.get('review_reason') else (None, '图片的显示变换尚不支持，未用未校验的图片代替原文。')
                    segment.update(type='image', src=src, alt='源文档图片（缩略图）') if src else segment.update(type='placeholder', text='【此处图片无法预览】' + error)
                else:
                    segment.update(type='placeholder', text='【此处原始对象缺失，无法预览】')
                field['segments'].append(segment)
                cursor = match.end()
            if cursor < len(text):
                field['segments'].append({'type': 'text', 'text': text[cursor:], 'problem_ids': []})
            if not field['segments']:
                field['segments'].append({'type': 'text', 'text': '解析无' if key == 'analysis' else '（未提取到内容）', 'problem_ids': []})
            question['fields'].append(field)
        for problem in question['problems']:
            if problem['field'] not in {f['key'] for f in question['fields']} and problem['field'] != 'question':
                question['fields'].append({'key': problem['field'], 'label': problem['field_label'],
                    'problem_ids': [problem['id']], 'segments': [{'type': 'placeholder', 'text': '此字段没有提取到内容。', 'problem_ids': [problem['id']]}]})
        if any(s['type'] == 'latex' for f in question['fields'] for s in f['segments']):
            question['preview_notes'].append('公式无法显示时，可展开详情。')
        question['preview_notes'].append('高亮处为未导出原因；完整原文见源 Word。')
        result['questions'].append(question)
    if result['document_issues'] and not result['questions']:
        result['empty_message'] = '文档未能形成可靠题目结构，暂无逐题预览；请查看文件级原因并打开源 Word。'
    return result


def _embedded_math(unicode_font_url: str | None = None, include_unicode_font: bool = True) -> tuple[str, list[str]]:
    """Vendor-only KaTeX resources; unavailable assets leave explicit source text."""
    folder = Path(__file__).with_name('web') / 'vendor' / 'katex'
    try:
        css = (folder / 'katex.min.css').read_text(encoding='utf-8')
        def embed_font(match):
            relative = match.group(1).strip('\"\'')
            if not re.fullmatch(r'fonts/[A-Za-z0-9_-]+\.woff2', relative):
                raise ValueError('Unexpected vendor font reference')
            encoded = base64.b64encode((folder / relative).read_bytes()).decode('ascii')
            return 'url(data:font/woff2;base64,' + encoded + ')'
        css = re.sub(r'url\(([^)]+)\)', embed_font, css)
        if include_unicode_font:
            unicode_folder = folder.parent / 'fonts'
            font_css = (unicode_folder / 'display-font.css').read_text(encoding='utf-8')
            if unicode_font_url is None:
                font_data = (unicode_folder / 'NotoSansCJKsc-Regular.otf').read_bytes()
                unicode_font_url = 'data:font/otf;base64,' + base64.b64encode(font_data).decode('ascii')
            elif unicode_font_url != '../_display/NotoSansCJKsc-Regular.otf':
                raise ValueError('Unexpected offline font URL')
            css += font_css.replace('url(NotoSansCJKsc-Regular.otf)', 'url(' + unicode_font_url + ')')
        library = (folder / 'katex.min.js').read_text(encoding='utf-8')
        coverage = (folder.parent.parent / 'font-coverage.js').read_text(encoding='utf-8')
        compatibility = (folder.parent.parent / 'math-preview.js').read_text(encoding='utf-8')
        if '</script' in library.lower() or '</style' in css.lower():
            raise ValueError('Unexpected vendor HTML terminator')
        runner = r"""document.querySelectorAll('.known-formula').forEach(function(el){
const tex=el.dataset.latex;
try{
QaMathPreview.render(tex,el);
el.dataset.displayStatus='rendered';
}catch(error){
el.dataset.displayStatus='fallback';
el.textContent='〔公式暂时无法显示〕';
const detail=document.createElement('details');const title=document.createElement('summary');title.textContent='公式详情';
const code=document.createElement('code');code.textContent='$$'+tex+'$$';detail.append(title,code);el.append(detail);
}});"""
        return css, [library, coverage, compatibility, runner]
    except (OSError, ValueError):
        return '', []


def render_preview_html(preview: dict) -> str:
    """Standalone offline HTML. No local links, original XML or OLE included."""
    esc = lambda value: html.escape(str(value), quote=True)
    has_math = any(s.get('type') == 'latex' for q in preview.get('questions', []) for f in q.get('fields', []) for s in f.get('segments', []))
    css, scripts = _embedded_math(include_unicode_font=has_math)
    hashes = ' '.join("\'sha256-" + base64.b64encode(hashlib.sha256(script.encode()).digest()).decode() + "\'" for script in scripts)
    script_policy = hashes if hashes else "'none'"
    chunks = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; font-src data:; script-src ' + script_policy + '; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">',
        '<style>' + css + '.known-formula{display:inline-block;max-width:100%;overflow-x:auto;vertical-align:middle}</style>',
        '<title>未导出题目说明</title><style>body{font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#303743;background:#f7f7f5;margin:0;padding:24px}main{max-width:920px;margin:auto}article{background:white;border:1px solid #ddd;border-radius:16px;padding:24px;margin:22px 0}h1{font-size:25px}h2{font-size:21px}h3{font-size:16px;margin:18px 0 5px}.notice{color:#805a1c;background:#fff6e5;padding:14px;border-radius:10px}.content{white-space:pre-wrap;overflow-wrap:anywhere}.bad{background:#fff0eb;border-bottom:2px solid #c64a36;padding:3px}.problem{border-left:4px solid #c64a36;padding:10px 15px;margin:15px 0;background:#fff7f3}img{max-width:100%;max-height:420px;vertical-align:middle}figure{margin:12px 0}figcaption,small{font-size:12px;color:#666}code{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f3f8}details{margin-top:10px}pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#3556ba}nav{display:flex;gap:12px;flex-wrap:wrap}@media(max-width:600px){body{padding:12px}article{padding:16px}}</style><main>',
        '<h1>未导出题目说明</h1><p>' + esc(preview.get('source_name', '')) + '</p>',
        '<p class="notice">' + esc(preview.get('notice', '')) + '</p>',
        '<p>按源题号查看未导出原因。</p>']
    for problem in preview.get('document_issues', []):
        chunks.append('<div class="problem"><strong>' + esc(problem['label']) + '</strong><p>' + esc(problem['reason']) + '</p></div>')
    questions = preview.get('questions', [])
    if not questions:
        chunks.append('<p>' + esc(preview.get('empty_message', '暂无逐题预览。')) + '</p>')
    chunks.append('<nav>' + ''.join(f'<a href="#q-{index}">第 {esc(q["number"])} 题</a>' for index, q in enumerate(questions)) + '</nav>')
    for index, q in enumerate(questions):
        problem_numbers = {p['id']: n for n, p in enumerate(q['problems'], 1)}
        chunks.append(f'<article id="q-{index}"><h2>第 {esc(q["number"])} 题 · {esc(q["kind_label"])}</h2><p class="notice">未导出 · 仅供问题定位</p>')
        for field in q['fields']:
            chunks.append('<section><h3>' + esc(field['label']) + (' · 有问题' if field['problem_ids'] else '') + '</h3><div class="content">')
            for segment in field['segments']:
                for pid in segment.get('problem_ids', []):
                    number = problem_numbers.get(pid)
                    if number:
                        chunks.append(f'<a class="bad" href="#problem-{index}-{number}" aria-label="查看问题 {number}">〔问题 {number}〕</a>')
                css = ' class="bad"' if segment.get('problem_ids') or segment['type'] == 'placeholder' else ''
                if segment['type'] == 'image':
                    src = segment.get('src', '')
                    if re.fullmatch(r'data:image/png;base64,[A-Za-z0-9+/=]+', src):
                        chunks.append('<figure' + css + '><img src="' + src + '" alt="' + esc(segment.get('alt', '')) + '"><figcaption>' + esc(segment.get('alt', '')) + '</figcaption></figure>')
                elif segment['type'] == 'latex':
                    chunks.append('<span class="known-formula" data-latex="' + esc(segment.get('latex', '')) + '"><small>〔公式加载中〕</small><code>' + esc(segment.get('text', '')) + '</code></span>')
                else:
                    chunks.append('<span' + css + '>' + esc(segment.get('text', '')) + '</span>')
            chunks.append('</div></section>')
        chunks.append('<h3>为什么没有导出</h3>')
        for number, problem in enumerate(q['problems'], 1):
            chunks.append(f'<div class="problem" id="problem-{index}-{number}"><strong>问题 {number} · ' + esc(problem['field_label'] + '：' + problem['label']) + '</strong><p>' + esc(problem['impact']) + '</p><details><summary>展开诊断信息</summary><p>' + esc(problem['reason']) + '</p><small>' + esc(problem['source_label']) + '</small><pre>' + esc(problem['code'] + '\n' + problem['source'] + '\n' + problem['diagnostic']) + '</pre></details></div>')
        chunks.append('</article>')
    chunks.append('</main>')
    chunks.extend('<script>' + script + '</script>' for script in scripts)
    chunks.append('</html>')
    return ''.join(chunks)
