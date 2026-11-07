#!/usr/bin/env python3
"""Synthetic-only native/frozen smoke; no private Reference inputs are read.

Run on Windows with --exe and optionally --gui. --development-only exercises the
fixtures on other platforms and is explicitly not Windows acceptance evidence.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import struct
import sys
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
M = 'http://schemas.openxmlformats.org/officeDocument/2006/math'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
S = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
HEADERS = {
    'choice': ['ID', '类型', '困难程度', '题目', '选择项', '答案', '解析', 'sid', 'optionA', 'optionB', 'optionC', 'optionD', 'title', 'questionType'],
    'blank': ['ID', '考点', '题目', '答案'],
}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def paragraph(text):
    return '<w:p><w:r><w:t xml:space="preserve">' + escape(text) + '</w:t></w:r></w:p>'


def write_docx(path: Path, body: str, png: bytes | None = None):
    with ZipFile(path, 'w') as z:
        z.writestr('word/document.xml', f'<w:document xmlns:w="{W}" xmlns:m="{M}" xmlns:r="{R}" xmlns:a="{A}"><w:body>{body}</w:body></w:document>')
        if png:
            z.writestr('word/media/synthetic.png', png)
            z.writestr('word/_rels/document.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="media/synthetic.png" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"/></Relationships>')
    return path


def create_inputs(directory: Path):
    from PIL import Image, ImageDraw
    directory.mkdir(parents=True)
    image = Image.new('RGB', (48, 32), 'white')
    draw = ImageDraw.Draw(image)
    draw.rectangle((5, 5, 42, 26), outline='black', width=2)
    draw.line((5, 5, 42, 26), fill='blue', width=2)
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    fraction = '<m:oMath><m:f><m:num><m:r><m:t>U</m:t></m:r></m:num><m:den><m:r><m:t>I</m:t></m:r></m:den></m:f></m:oMath>'
    body = paragraph('一、单选题') + '<w:p><w:r><w:t>1．选择与电阻</w:t></w:r>' + fraction + '<w:r><w:t>对应的图示。</w:t></w:r></w:p>'
    body += '<w:p><w:r><w:t>A．图示</w:t></w:r><w:r><w:drawing><a:blip r:embed="rId1"/></w:drawing></w:r></w:p>'
    body += paragraph('B．乙') + paragraph('C．丙') + paragraph('D．丁') + paragraph('【答案】A') + paragraph('【分析】合成分析文本。') + paragraph('【详解】合成详解文本。')
    choice = write_docx(directory / '合成 选择.docx', body, stream.getvalue())
    blank = write_docx(directory / '合成 填空.docx', paragraph('一、填空题') + paragraph('1．公共题干：按顺序填写。') + paragraph('(1)第一空____') + paragraph('(2)第二空____') + paragraph('【答案】(1)铁') + paragraph('(2)铜'))
    broken = directory / '合成 损坏.docx'
    broken.write_bytes(b'intentionally invalid synthetic DOCX')
    return {'choice': choice, 'blank': blank, 'broken': broken}, stream.getvalue()


def synthetic_wmf() -> bytes:
    """Original synthetic drawing, no copied/private MathType or Word object."""
    def record(op, data=b''):
        data += b'\0' * (len(data) % 2)
        return struct.pack('<IH', 3 + len(data) // 2, op) + data
    records = [record(0x020B, struct.pack('<hh', 0, 0)),
               record(0x020C, struct.pack('<hh', 200, 600)),
               record(0x02FA, struct.pack('<HhhI', 0, 3, 0, 0)),
               record(0x012D, struct.pack('<H', 0)),
               record(0x0214, struct.pack('<hh', 150, 30)),
               record(0x0213, struct.pack('<hh', 150, 550))]
    font = struct.pack('<hhhhhBBBBBBBB', -70, 0, 0, 0, 400, 0, 0, 0, 0, 0, 0, 0, 0) + b'Times New Roman\0'
    records += [record(0x02FB, font), record(0x012D, struct.pack('<H', 1)),
                record(0x0A32, struct.pack('<hhHH', 20, 30, 6, 0) + b'R = 12'), record(0)]
    body = b''.join(records)
    header = struct.pack('<HHHIHIH', 1, 9, 0x300, (18 + len(body)) // 2, 2, max(len(r) // 2 for r in records), 0)
    placeable = struct.pack('<IHhhhhHI', 0x9AC6CDD7, 0, 0, 0, 600, 200, 1440, 0)
    checksum = 0
    for word in struct.unpack('<10H', placeable):
        checksum ^= word
    return placeable + struct.pack('<H', checksum) + header + body


def read_cells(path: Path):
    with ZipFile(path) as z:
        require(z.testzip() is None, 'Invalid XLSX CRC')
        shared = []
        if 'xl/sharedStrings.xml' in z.namelist():
            shared = [''.join(si.itertext()) for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('s:si', S)]
        rows = []
        for row in ET.fromstring(z.read('xl/worksheets/sheet1.xml')).findall('.//s:row', S):
            values = {}
            for cell in row.findall('s:c', S):
                letters = ''.join(c for c in cell.attrib['r'] if c.isalpha())
                column = 0
                for c in letters:
                    column = column * 26 + ord(c) - 64
                value = cell.findtext('s:v', '', S)
                if cell.attrib.get('t') == 's':
                    value = shared[int(value)]
                elif cell.attrib.get('t') == 'inlineStr':
                    value = ''.join(cell.find('s:is', S).itertext())
                values[column - 1] = value
            rows.append([values.get(i, '') for i in range(max(values, default=-1) + 1)])
        return rows


def check_run(folder: Path, kind: str, original_png: bytes):
    summary = json.loads((folder / 'summary.json').read_text(encoding='utf-8'))
    require(summary['status'] == 'completed' and summary['total'] == summary['passed'] == 1 and summary['quarantined'] == 0, f'{kind}: failed or incomplete conversion')
    require('export_preview_error' not in summary, 'Offline preview generation failed')
    rows = read_cells(folder / 'export/questions.xlsx')
    require(rows[0] == HEADERS[kind] and len(rows) == 2, 'Header/order/large-question row count differs')
    # Empty trailing XLSX cells may be omitted in the XML; normalize only trailing blanks.
    row = dict(zip(HEADERS[kind], rows[1] + [''] * len(HEADERS[kind])))
    require(row['ID'] == '', 'Unspecified ID must remain blank')
    if kind == 'choice':
        require(r'$$\frac{U}{I}$$' in row['题目'], 'OMML fraction lost or altered')
        require(row['答案'] == 'A', 'Answer ownership changed')
        require('合成分析文本。' in row['解析'] and '合成详解文本。' in row['解析'], 'Analysis/detail merge lost content')
        require(row['困难程度'] in ['低', '中', '高'], 'Difficulty outside configured values')
        require('请参考 0001.png' in row['optionA'] and '请参考 0001.png' in row['选择项'], 'Image option reference missing')
        require((folder / 'export/images/0001.png').read_bytes() == original_png, 'Original PNG bytes changed')
        require(summary['exported_formulas']['converted'] == 1, 'Expected one real OMML conversion')
    else:
        require(row['考点'] == '', 'Default knowledge point must remain blank')
        require('(1)第一空____' in row['题目'] and '(2)第二空____' in row['题目'], 'Subquestions lost')
        require(row['答案'] == '(1)铁\n(2)铜', 'Subquestion answers lost or reordered')
    html = (folder / 'export/题目预览.html').read_text(encoding='utf-8')
    require('katex' in html and (folder / '_display/NotoSansCJKsc-Regular.otf').is_file(), 'Offline math/font assets missing')
    task = json.loads((folder / 'task/task.json').read_text(encoding='utf-8'))
    return {'cells': rows, 'questions': [{k: q.get(k) for k in ['number', 'stem', 'options', 'answer', 'analysis', 'difficulty', 'knowledge_point', 'asset_filenames', 'issues']} for q in task['questions']],
            'images': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (folder / 'export/images').iterdir()}}


def check_batch(folder: Path):
    summary = json.loads((folder / 'batch-summary.json').read_text(encoding='utf-8'))
    require(summary['status'] == 'success' and summary['passed'] == 2 and summary['quarantined'] == 0, 'Synthetic batch incomplete')
    with ZipFile(summary['delivery']) as z:
        require(z.testzip() is None, 'Final ZIP CRC failure')
        names = z.namelist()
        require(len([n for n in names if n.endswith('.xlsx')]) == 2, 'Batch must contain two independent workbooks')
        require(len([n for n in names if n.endswith('题目预览.html')]) == 2, 'Batch previews missing')
        require(not any(n.endswith(('.json', '.log', '.docx', '.xml', '.bin')) for n in names), 'Internal diagnostics/source leaked into delivery ZIP')
        require('_display/NotoSansCJKsc-Regular.otf' in names and '_display/OFL.txt' in names, 'Shared font or license missing')
        require(len([n for n in names if '/images/' in n]) == 1, 'Synthetic image count differs')
    return {'questions': 2, 'zip_crc': 'passed', 'xlsx_files': 2, 'internal_files_excluded': True}


def sha_file(path: Path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def file_inventory(directory: Path):
    inventory = {}
    for path in sorted(directory.rglob('*')):
        require(not path.is_symlink(), f'Unexpected bundle symlink: {path}')
        if path.is_file():
            inventory[path.relative_to(directory).as_posix()] = sha_file(path)
    return inventory


def check_pe_x64(path: Path):
    with path.open('rb') as stream:
        header = stream.read(64)
        require(header[:2] == b'MZ' and len(header) == 64, f'Not a native PE executable: {path}')
        stream.seek(struct.unpack_from('<I', header, 60)[0])
        pe = stream.read(6)
    require(pe[:4] == b'PE\0\0' and struct.unpack_from('<H', pe, 4)[0] == 0x8664, f'Not Windows x64: {path}')


def relocate_bundle(executable: Path, output: Path):
    source = executable.resolve().parent
    before = file_inventory(source)
    require(before, 'Empty portable bundle')
    require(not any(name.endswith(('.dylib', '.so')) or any(part.endswith('.app') for part in Path(name).parts) for name in before), 'Mac/Unix native resources leaked into Windows package')
    web = source / '_internal/qa_parser/web'
    required_web = ROOT / 'src/qa_parser/web'
    checked = 0
    for original in required_web.rglob('*'):
        if original.is_file() and '__pycache__' not in original.parts:
            target = web / original.relative_to(required_web)
            require(target.is_file(), f'Missing frozen display resource: {target}')
            require(hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(original.read_bytes()).digest(), f'Frozen display resource differs: {target}')
            checked += 1
    native = [executable.resolve(), source / '_internal/wmf/bin/wmf2gd.exe', source / '_internal/webview2/msedgewebview2.exe']
    for binary in native:
        require(binary.is_file(), f'Missing bundled native executable: {binary}')
        check_pe_x64(binary)
    require((source / '_internal/wmf/fonts/fontmap').is_file(), 'Bundled WMF fontmap missing')
    manifest_path = source / '_internal/resources-manifest.json'
    require(manifest_path.is_file(), 'Native resource integrity manifest missing')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    require(manifest.get('schema_version') == 1 and manifest.get('platform') == 'win32' and manifest.get('architecture') == 'x64', 'Unexpected resource manifest format/platform')
    listed = set()
    for entry in manifest['files']:
        name = entry['path']
        require(isinstance(name, str) and not name.startswith('/') and '\\' not in name and ':' not in name and '..' not in name.split('/'), 'Unsafe manifest path')
        require(name not in listed, 'Duplicate native resource manifest entry')
        listed.add(name)
        target = source / '_internal' / name
        require(target.is_file() and target.stat().st_size == entry['bytes'] and sha_file(target) == entry['sha256'], f'Native resource changed or missing: {name}')
    actual = {name.removeprefix('_internal/') for name in before if name.startswith(('_internal/wmf/', '_internal/webview2/', '_internal/licenses/'))}
    require(listed == actual, 'Native resource manifest does not cover staged runtime exactly')
    archive = output / 'portable-candidate.zip'
    with ZipFile(archive, 'x', compression=ZIP_DEFLATED, compresslevel=6) as z:
        for name in before:
            z.write(source / name, source.name + '/' + name)
    with ZipFile(archive) as z:
        require(z.testzip() is None, 'Portable executable ZIP failed CRC')
        z.extractall(output / '中文 解压')
    relocated = output / '中文 解压' / source.name
    require(file_inventory(relocated) == before, 'Unpacked application differs from built application')
    return relocated / executable.name, before, {'zip_sha256': sha_file(archive),
        'file_count': len(before), 'native_manifest_files': len(listed), 'display_files_compared_to_source': checked, 'pe_x64_executables_checked': 3,
        'relocation': 'ZIP unpacked to Chinese and space path; file hashes equal original', 'archive': str(archive)}


def tail_text(path: Path, limit=8192):
    """Read bounded diagnostic tails without loading entire runner logs."""
    try:
        with path.open('rb') as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - limit))
            return stream.read(limit).decode('utf-8', errors='replace')
    except OSError as error:
        return f'[{type(error).__name__}: diagnostic file unavailable]'


def failure_details(arguments, log: Path, internal: Path):
    sections = [f'{path.name} (last 8192 bytes):\n{tail_text(path)}' for path in (internal, log)]
    if '--out' in arguments:
        index = arguments.index('--out')
        if index + 1 < len(arguments):
            directory = Path(arguments[index + 1])
            for filename in ('summary.json', 'batch-summary.json'):
                path = directory / filename
                if not path.is_file():
                    continue
                try:
                    if path.stat().st_size > 1024 * 1024:
                        text = '[summary exceeds 1 MiB; bounded tail]\n' + tail_text(path)
                    else:
                        report = json.loads(path.read_text(encoding='utf-8'))
                        selected = {key: report[key] for key in ('status', 'document_issues', 'user_result', 'delivery_error', 'message') if key in report}
                        if 'files' in report and isinstance(report['files'], list):
                            selected['files'] = [{key: row.get(key) for key in ('name', 'status', 'message', 'reasons', 'diagnostic')} for row in report['files']]
                        text = json.dumps(selected, ensure_ascii=False, indent=2)
                        if len(text.encode('utf-8')) > 8192:
                            text = text.encode('utf-8')[:8192].decode('utf-8', errors='replace') + '\n[summary diagnostic truncated]'
                    sections.append(f'{filename} failure summary:\n{text}')
                except (OSError, ValueError, TypeError) as error:
                    sections.append(f'{filename}: {type(error).__name__}; tail:\n{tail_text(path)}')
    return '\n\n'.join(sections)


def execute(command, args, cwd, env, log: Path, expected=0, timeout=120):
    arguments = list(map(str, args))
    internal = log.with_suffix('.internal.log').resolve()
    child_env = dict(env, QA_PARSER_CLI_LOG=str(internal))
    try:
        with log.open('wb') as stream:
            result = subprocess.run([*command, *arguments], cwd=cwd, env=child_env, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as error:
        raise AssertionError(f'Command exceeded {timeout}s; see {log}\n{failure_details(arguments, log, internal)}') from error
    require(result.returncode == expected, f'Exit {result.returncode}, expected {expected}; see {log}\n' + failure_details(arguments, log, internal) if result.returncode != expected else '')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--gui', action='store_true')
    parser.add_argument('--development-only', action='store_true')
    args = parser.parse_args(argv)
    if not args.development_only and (sys.platform != 'win32' or not args.exe):
        parser.error('Native Windows and --exe are required unless --development-only is explicit')
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {'schema_version': 1, 'status': 'failed', 'platform': platform.platform(), 'scope': 'synthetic OMML, PNG, choice, multi-part blank, corrupt input, batch export',
              'private_reference': 'not accessed; nine-document Windows acceptance NOT performed', 'windows_native': sys.platform == 'win32' and not args.development_only,
              'customer_environment': 'unverified', 'gui': 'not requested', 'runs': {}}
    try:
        executable = args.exe.resolve() if args.exe else None
        bundle_before = None
        if executable:
            executable, bundle_before, report['resources'] = relocate_bundle(executable, out)
        inputs, png = create_inputs(out / '合成 输入')
        env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        commands = {'development': [sys.executable, '-m', 'qa_parser']}
        if args.exe:
            commands['frozen'] = [str(executable), '--cli']
        baselines = {}
        for name, command in commands.items():
            base = out / name
            base.mkdir()
            run_env = dict(env)
            if name == 'development':
                run_env['PYTHONPATH'] = str(ROOT / 'src')
            else:
                run_env.pop('PYTHONPATH', None)
                run_env.pop('PYTHONHOME', None)
                for key in list(run_env):
                    if key.startswith('QA_PARSER_'):
                        run_env.pop(key)
                system = Path(run_env.get('SystemRoot') or run_env['SYSTEMROOT'])
                run_env['PATH'] = os.pathsep.join(map(str, [system / 'System32', system]))
            for kind in ['choice', 'blank']:
                folder = base / ('中文 ' + kind)
                execute(command, ['run', inputs[kind], '--kind', kind, '--out', folder], base, run_env, base / (kind + '.log'))
                semantic = check_run(folder, kind, png)
                if name == 'development':
                    baselines[kind] = semantic
                else:
                    require(semantic == baselines[kind], f'Development/frozen {kind} semantic mismatch')
            broken = base / '损坏结果'
            execute(command, ['run', inputs['broken'], '--kind', 'choice', '--out', broken], base, run_env, base / 'broken.log', expected=1)
            require(not (broken / 'export/questions.xlsx').exists(), 'Corrupt source produced fake successful workbook')
            batch = base / '批量 结果'
            execute(command, ['batch', inputs['choice'], inputs['blank'], '--out', batch], base, run_env, base / 'batch.log')
            report['runs'][name] = check_batch(batch)
        if args.exe:
            wmf_input = out / 'synthetic-original.wmf'
            wmf_input.write_bytes(synthetic_wmf())
            wmf_report = out / 'native-wmf.json'
            execute([str(executable)], ['--runtime-smoke', wmf_input, wmf_report], out, run_env, out / 'native-wmf.log')
            native = json.loads(wmf_report.read_text(encoding='utf-8'))
            require(native['status'] == 'converted', 'Bundled WMF runtime did not produce valid image')
            require(native['width'] > 0 and native['height'] > 0, 'WMF output dimensions invalid')
            report['wmf_runtime'] = native
        if args.gui:
            require(args.exe is not None and not args.development_only, 'GUI smoke needs frozen Windows executable')
            gui_report = out / 'native-window.json'
            execute([str(executable)], ['--desktop-smoke', gui_report], out, run_env, out / 'native-window.log', timeout=90)
            gui = json.loads(gui_report.read_text(encoding='utf-8'))
            require(gui['status'] == 'passed', 'Native window smoke did not pass')
            report['gui'] = gui
        if executable:
            require(file_inventory(executable.parent) == bundle_before, 'Application wrote or changed files inside portable program directory')
            report['program_directory_content_unchanged'] = True
        report['status'] = 'passed'
        report['frozen_matches_development'] = bool(args.exe)
        report['restricted_frozen_path'] = bool(args.exe)
        return 0
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        (out / 'smoke-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    raise SystemExit(main())
