"""Read-only, offline display of the actual exported workbook.

The HTML lives beside questions.xlsx and images/. It does not include source
DOCX/OLE/XML, does not repair formulas, and never invokes a rejected renderer.
Images remain relative files so repeated occurrences do not inflate the HTML.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
from pathlib import Path
import re
from urllib.parse import quote
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from .problem_preview import _embedded_math

MAX_XML_BYTES = 64 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 12_000_000
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def _xml(archive, name):
    info = archive.getinfo(name)
    if info.file_size > MAX_XML_BYTES:
        raise ValueError('Workbook preview XML exceeds size limit')
    data = archive.read(name)
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('Workbook preview XML must not contain DTD/entities')
    return ET.fromstring(data)


def _workbook_rows(path, sheet_index=1):
    """Read literal XLSX cell strings, not a second interpretation of DOCX."""
    if path.is_symlink() or not path.is_file():
        raise ValueError('Preview workbook must be a regular file')
    with ZipFile(path) as archive:
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            strings = [''.join(t.text or '' for t in si.findall('.//s:t', NS))
                       for si in _xml(archive, 'xl/sharedStrings.xml')]
        if not isinstance(sheet_index, int) or not 1 <= sheet_index <= 16:
            raise ValueError('Invalid worksheet index')
        root = _xml(archive, f'xl/worksheets/sheet{sheet_index}.xml')
    if root.findall('.//s:f', NS):
        raise ValueError('Unexpected executable formula in exported workbook')
    result = []
    for row in root.findall('.//s:sheetData/s:row', NS):
        values = {}
        for cell in row.findall('s:c', NS):
            address = re.fullmatch(r'([A-Z]+)([1-9][0-9]*)', cell.get('r', ''))
            if not address:
                raise ValueError('Invalid workbook cell address')
            col = 0
            for letter in address[1]:
                col = col * 26 + ord(letter) - 64
            if col > 16384 or col in values:
                raise ValueError('Duplicate/out-of-range workbook column')
            kind = cell.get('t')
            if kind == 'inlineStr':
                value = ''.join(t.text or '' for t in cell.findall('.//s:t', NS))
            elif kind == 's':
                index = int(cell.findtext('s:v', default='-1', namespaces=NS))
                if not 0 <= index < len(strings):
                    raise ValueError('Invalid shared string reference')
                value = strings[index]
            else:
                value = cell.findtext('s:v', default='', namespaces=NS)
                if value:
                    raise ValueError('Export preview expects literal text cells')
            values[col] = value
        result.append((int(row.get('r', '0')), [values.get(i, '') for i in range(1, max(values, default=0) + 1)]))
    if not result or result[0][0] != 1:
        raise ValueError('Export workbook has no header')
    return result


def _images(root, manifest):
    from PIL import Image
    output = {}
    for entry in manifest.get('images', []):
        name = entry.get('filename')
        if (not isinstance(name, str) or not name or name in {'.', '..'}
                or '/' in name or '\\' in name or name in output):
            raise ValueError('Unsafe or duplicated preview image filename')
        path = root / 'images' / name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError('Preview image missing/outside export directory')
        if path.stat().st_size > MAX_IMAGE_BYTES:
            output[name] = {'type': 'placeholder', 'text': '【图片超过安全预览大小，原文件仍保存在 images 中：' + name + '】'}
            continue
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry.get('sha256'):
            raise ValueError('Preview image integrity mismatch: ' + name)
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                if (image.format not in {'PNG', 'JPEG', 'GIF', 'WEBP', 'BMP'}
                        or image.width * image.height > MAX_IMAGE_PIXELS
                        or getattr(image, 'n_frames', 1) != 1):
                    raise ValueError('Image format/size/frame count not supported by offline preview')
                expected_extensions = {'PNG': {'.png'}, 'JPEG': {'.jpg', '.jpeg'}, 'GIF': {'.gif'},
                                       'WEBP': {'.webp'}, 'BMP': {'.bmp'}}[image.format]
                if path.suffix.lower() not in expected_extensions:
                    raise ValueError('Image extension differs from real format')
                output[name] = {'type': 'image', 'src': 'images/' + quote(name, safe=''),
                                'filename': name, 'width': image.width, 'height': image.height}
        except (OSError, ValueError):
            output[name] = {'type': 'placeholder', 'text': '【图片无法预览，原文件：' + name + '】'}
    return output


def build_export_preview(export_dir: Path, source_name: str = '', *,
                         formula_delimiter: str = '$$', image_reference: str | None = None) -> dict:
    root = Path(export_dir).resolve()
    manifest = json.loads((root / 'report.json').read_text(encoding='utf-8'))
    if manifest.get('schema_version') != 1:
        raise ValueError('Unsupported export manifest schema')
    sheets = manifest.get('sheets') or [{'name': '', 'index': 1}]
    mappings = manifest.get('rows', [])
    records = []
    for sheet in sheets:
        rows = _workbook_rows(root / 'questions.xlsx', sheet['index'])
        columns = rows[0][1]
        if not columns or len(columns) != len(set(columns)):
            raise ValueError('Preview column names are empty or duplicated')
        sheet_mappings = [m for m in mappings if m.get('sheet', '') == sheet['name']]
        if len(rows) - 1 != len(sheet_mappings):
            raise ValueError('Workbook rows and source-number mapping differ')
        records.extend((row, mapping, columns) for row, mapping in zip(rows[1:], sheet_mappings))
    if len(records) != len(mappings) or len(mappings) != manifest.get('exported_questions'):
        raise ValueError('Workbook rows and source-number mapping differ')
    if not isinstance(formula_delimiter, str) or not 1 <= len(formula_delimiter) <= 16:
        raise ValueError('Invalid formula delimiter for display')
    pictures = _images(root, manifest)
    if image_reference is None and 'image_reference_map' in manifest:
        aliases = manifest['image_reference_map']
        if (not isinstance(aliases, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in aliases.items())
            or set(aliases.values()) != set(pictures) or len(aliases) != len(pictures)
            or any(ref != manifest['image_reference'].format(filename=name) for ref, name in aliases.items())):
            raise ValueError('Invalid image reference mapping')
        references = {ref: pictures[name] for ref, name in aliases.items()}
    else:
        if image_reference is None:
            image_reference = manifest.get('image_reference', '参考{filename}')
        references = {image_reference.format(filename=name): asset for name, asset in pictures.items()}
    if len(references) != len(pictures) or any(not value for value in references):
        raise ValueError('Image reference format is ambiguous')
    delimiter = re.escape(formula_delimiter)
    image_pattern = '|'.join(re.escape(value) for value in sorted(references, key=len, reverse=True))
    pattern = re.compile('(?P<formula>' + delimiter + r'.*?' + delimiter + ')' +
                         ('|(?P<image>' + image_pattern + ')' if image_pattern else ''), re.S)
    counts = {'questions': len(mappings), 'formulas': 0, 'image_occurrences': 0,
              'image_placeholders': 0, 'omitted_questions': len(manifest.get('omitted_questions', []))}

    def segments(value):
        result, cursor = [], 0
        for match in pattern.finditer(value):
            if match.start() > cursor:
                result.append({'type': 'text', 'text': value[cursor:match.start()]})
            if match.lastgroup == 'formula':
                result.append({'type': 'latex', 'latex': match[0][len(formula_delimiter):-len(formula_delimiter)], 'text': match[0]})
                counts['formulas'] += 1
            else:
                asset = references[match[0]]
                result.append({'type': 'text', 'text': match[0]})
                result.append(dict(asset))
                counts['image_occurrences'] += 1
                counts['image_placeholders'] += asset['type'] == 'placeholder'
            cursor = match.end()
        if cursor < len(value):
            result.append({'type': 'text', 'text': value[cursor:]})
        return result or [{'type': 'text', 'text': '（空）'}]

    questions = []
    for (excel_row, cells), mapping, columns in records:
        if mapping.get('excel_row') != excel_row or len(cells) != len(columns):
            raise ValueError('Workbook row differs from source mapping/template width')
        values = dict(zip(columns, cells))
        primary = ['题目']
        option_columns = [key for key in columns if re.fullmatch(r'option[A-Z]', key)]
        primary += option_columns or (['选择项'] if '选择项' in values else [])
        primary += ['答案', '解析', '考点']
        fields = [{'key': key, 'label': '选项 ' + key[6:] if key.startswith('option') else key,
                   'segments': segments(values[key])} for key in primary if key in values]
        questions.append({'number': str(mapping.get('number', '?')), 'excel_row': excel_row, 'sheet': mapping.get('sheet', ''),
                          'fields': fields, 'difficulty': values.get('困难程度', ''),
                          'cells': list(zip(columns, cells))})
    return {'schema_version': 1, 'source_name': source_name or manifest.get('source', {}).get('name', ''),
            'kind': manifest.get('kind'), 'questions': questions, 'counts': counts,
            'omitted_numbers': [str(q.get('number', '?')) for q in manifest.get('omitted_questions', [])],
            'workbook_sha256': hashlib.sha256((root / 'questions.xlsx').read_bytes()).hexdigest()}


def render_export_preview(export_dir: Path, source_name: str = '', **options) -> tuple[str, dict]:
    """Return portable HTML and bounded display-accounting metadata.

    Save as 题目预览.html beside the exported images/ directory. Dynamic math
    success counts are measured by the shared renderer, never inferred here.
    """
    preview = build_export_preview(export_dir, source_name, **options)
    esc = lambda value: html.escape(str(value), quote=True)
    math_css, scripts = _embedded_math(unicode_font_url='../_display/NotoSansCJKsc-Regular.otf')
    if not scripts:
        raise ValueError('Bundled offline formula display runtime unavailable')
    summary_script = """(async function(){const eFont=document.getElementById('formula-status');eFont.textContent='正在加载公式…';try{const fonts=await document.fonts.load('16px \"QA Formula Unicode\"');await document.fonts.ready;if(!fonts.length||fonts.some(f=>f.status!=='loaded'))throw new Error('font unavailable');eFont.dataset.fontStatus='loaded';}catch(error){eFont.textContent='公式字体加载失败，请完整解压结果包后重试。';eFont.dataset.fontStatus='failed';return;}const nodes=Array.from(document.querySelectorAll('.known-formula'));const rendered=nodes.filter(n=>n.dataset.displayStatus==='rendered'||(n.querySelector('.katex')&&!n.querySelector('.katex-error'))).length;const fallback=nodes.length-rendered;const e=document.getElementById('formula-status');e.textContent='公式显示：'+rendered+' / '+nodes.length+' 处'+(fallback?'；'+fallback+' 处无法显示':'');e.dataset.rendered=String(rendered);e.dataset.fallback=String(fallback);document.querySelectorAll('img.source-image').forEach(function(img){function failed(){if(img.dataset.displayStatus==='fallback')return;img.dataset.displayStatus='fallback';const note=document.createElement('span');note.className='placeholder';note.textContent='【图片加载失败：'+img.alt+'。请完整解压并保留 images 文件夹。】';img.after(note);img.hidden=true;}img.addEventListener('error',failed);if(img.complete&&!img.naturalWidth)failed();});})();"""
    scripts = [*scripts, summary_script]
    hashes = ' '.join("'sha256-" + base64.b64encode(hashlib.sha256(script.encode()).digest()).decode() + "'" for script in scripts)
    policy = "default-src 'none'; img-src 'self' data:; font-src 'self' data:; script-src " + hashes + "; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
    chunks = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
              '<meta name="viewport" content="width=device-width, initial-scale=1">',
              '<meta http-equiv="Content-Security-Policy" content="' + esc(policy) + '">',
              '<title>' + esc(preview['source_name']) + ' · 题目预览</title><style>' + math_css + '.source-image[hidden]{display:none}</style>',
              '''<style>*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#f6f7f5;color:#27322d;font:16px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}main{max-width:1060px;margin:auto;padding:28px 26px 70px}header{margin-bottom:22px}h1{font-size:27px;margin:0 0 8px;line-height:1.35}h2{font-size:21px;margin:0}h3{font-size:14px;color:#647168;margin:18px 0 6px}.meta,.notice,small{color:#69746e}.notice{padding:12px 16px;border-left:3px solid #799882;background:#edf3ef}.layout{display:grid;grid-template-columns:130px minmax(0,1fr);gap:24px}nav{position:sticky;top:18px;align-self:start;max-height:calc(100vh - 36px);overflow:auto;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;padding:5px}nav a{padding:5px 3px;text-align:center;text-decoration:none;background:white;border:1px solid #dfe6df;border-radius:7px;color:#41674e}article{background:white;border:1px solid #dfe6df;border-radius:16px;padding:24px;margin-bottom:22px;scroll-margin-top:20px;min-width:0}.question-head{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}.content{white-space:pre-wrap;overflow-wrap:anywhere}.known-formula{display:inline-block;max-width:100%;overflow-x:auto;vertical-align:middle}.source-image{display:block;max-width:100%;height:auto;max-height:720px;object-fit:contain;object-position:left;margin:10px 0}figure{margin:10px 0}figcaption{font-size:12px;color:#778079}.placeholder{display:block;padding:10px;background:#fff3df;border-left:3px solid #bc9143}details{margin-top:18px;border-top:1px solid #edf0eb;padding-top:12px;font-size:13px}summary{cursor:pointer;color:#61786a}table{border-collapse:collapse;width:100%;table-layout:fixed}th,td{border-bottom:1px solid #edf0eb;text-align:left;vertical-align:top;padding:7px;overflow-wrap:anywhere;white-space:pre-wrap}th{width:90px;font-weight:500}code{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#41674e}@media(max-width:700px){main{padding:18px 12px}.layout{grid-template-columns:1fr;gap:12px}nav{position:static;max-height:125px;grid-template-columns:repeat(auto-fill,minmax(45px,1fr))}article{padding:18px}h1{font-size:23px}}@media print{nav{display:none}.layout{display:block}body{background:white}article{break-inside:avoid}main{padding:0}}</style></head><body><main>''',
              '<header><h1>' + esc(preview['source_name']) + '</h1><div class="meta">已导出题目 · ' + str(preview['counts']['questions']) + ' 题</div>',
              '<p class="notice">已导出题目 · 请完整解压结果包后打开。</p>',
              '<p id="formula-status" role="status">正在加载公式…</p>']
    if preview['omitted_numbers']:
        chunks.append('<p class="notice">未导出源题号：' + esc('、'.join(preview['omitted_numbers'])) + '。这些题目不在本页和 Excel 中；详见本包结果说明。</p>')
    chunks.extend(['</header><div class="layout"><nav aria-label="跳转到源题号">',
                   ''.join('<a href="#q-' + str(index) + '">' + esc(q['number']) + '</a>' for index, q in enumerate(preview['questions'])), '</nav><div>'])
    for index, question in enumerate(preview['questions']):
        chunks.append('<article id="q-' + str(index) + '"><div class="question-head"><h2>源第 ' + esc(question['number']) + ' 题</h2><small>Excel 第 ' + str(question['excel_row']) + ' 行 · ' + esc(question.get('sheet', '')) + (' · 难度 ' + esc(question['difficulty']) if question['difficulty'] else '') + '</small></div>')
        for field in question['fields']:
            chunks.append('<section><h3>' + esc(field['label']) + '</h3><div class="content">')
            for segment in field['segments']:
                if segment['type'] == 'latex':
                    chunks.append('<span class="known-formula" data-latex="' + esc(segment['latex']) + '"><code>' + esc(segment['text']) + '</code></span>')
                elif segment['type'] == 'image':
                    chunks.append('<figure><img class="source-image" width="' + str(segment['width']) + '" height="' + str(segment['height']) + '" src="' + esc(segment['src']) + '" alt="' + esc(segment['filename']) + '"><figcaption>' + esc(segment['filename']) + '</figcaption></figure>')
                else:
                    chunks.append('<span' + (' class="placeholder"' if segment['type'] == 'placeholder' else '') + '>' + esc(segment['text']) + '</span>')
            chunks.append('</div></section>')
        chunks.append('<details><summary>查看 Excel 原始列值</summary><table>')
        chunks.extend('<tr><th>' + esc(name) + '</th><td>' + esc(value) + '</td></tr>' for name, value in question['cells'])
        chunks.append('</table></details></article>')
    chunks.append('</div></div></main>')
    chunks.extend('<script>' + script + '</script>' for script in scripts)
    chunks.append('</body></html>')
    report = {**preview['counts'], 'workbook_sha256': preview['workbook_sha256'],
              'dynamic_formula_display': 'measured by shared offline renderer; not certified by HTML generation',
              'image_mode': 'verified relative files in images/', 'source_files_included': False}
    return ''.join(chunks), report
