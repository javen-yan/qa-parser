"""Publish a portable customer package using an explicit file allowlist.

Working files, source snapshots and formula evidence never enter this package.
All referenced images are checked against the export manifest before publishing.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import unicodedata
from urllib.parse import quote
from zipfile import ZIP_DEFLATED, ZipFile


def validate_delivery_names(names) -> None:
    """Keep basenames exactly, rejecting collisions on Windows and macOS."""
    def key(name):
        return unicodedata.normalize('NFC', name).casefold()
    paths = {key(name) for name in ('_说明', '_display', '批次结果说明.txt', '转换失败说明.txt')}
    for name in names:
        stem = Path(name).stem
        if (not stem or stem in {'.', '..'} or stem.endswith((' ', '.'))
                or re.search(r'[<>:"/\\|?*\x00-\x1f]', stem)
                or re.match(r'(?i)^(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\.|$)', stem)
                or len((stem + '.xlsx').encode('utf-16-le')) // 2 > 255):
            raise ValueError(f'文件名无法跨平台原样交付，请先重命名 Word：{name}')
        targets = {key(stem), key(stem + '.xlsx')}
        if targets & paths:
            raise ValueError(f'文件名重复或交付路径冲突，请先重命名 Word：{name}')
        paths.update(targets)


def _document_report(row: dict) -> str:
    lines = [row['name'], row['message']]
    total = row.get('total')
    if total is None:
        lines.append('题目总数及未导出题号：无法读取或识别，不能确认数量。')
    else:
        lines.append(f"共 {total} 题，已导出 {row.get('passed') or 0} 题，未导出 {row.get('quarantined') or 0} 题。")
    reasons = row.get('reasons', [])
    if reasons:
        lines.append('原因：' + '；'.join(reasons))
    affected = row.get('affected_questions', [])
    if affected:
        lines.append('未导出题目：')
        lines.extend(f"第 {q['number']} 题：{'；'.join(q['reasons'])}" for q in affected)
    elif row.get('quarantined'):
        lines.append('文档未能完整处理，未导出题号无法可靠确认。')
    if row.get('problem_preview'):
        lines.append('未导出题目及原因见“未导出题目.html”。')
    if row.get('preview_notice'):
        lines.append(row['preview_notice'])
    if row.get('export'):
        stem = Path(row['name']).stem
        lines.extend([f'题库位于压缩包根目录 {stem}.xlsx；图片位于根目录 {stem}/ 文件夹。',
                      f'“参考aaa0001.png)”等引用对应 {stem}/aaa0001.png；不同文件的图片编号独立。'])
    if row.get('export_preview'):
        lines.append('题目预览：题目预览.html。请保留整个解压目录。')
    return '\n'.join(lines) + '\n'


def _export_files(row: dict) -> list[tuple[Path, str]]:
    root = Path(row['export']).resolve()
    workbook = root / 'questions.xlsx'
    if workbook.is_symlink() or not workbook.is_file():
        raise ValueError('Export workbook is missing or not a regular file')
    manifest = json.loads((root / 'report.json').read_text(encoding='utf-8'))
    files = [(workbook, 'questions.xlsx')]
    seen = set()
    for image in manifest.get('images', []):
        name = image['filename']
        if not isinstance(name, str) or not name or name in {'.', '..'} or '/' in name or '\\' in name or name in seen:
            raise ValueError('Unsafe or duplicated image name in export manifest')
        seen.add(name)
        source = root / 'images' / name
        if source.is_symlink() or not source.resolve().is_relative_to(root) or not source.is_file():
            raise ValueError(f'Export image missing: {name}')
        if hashlib.sha256(source.read_bytes()).hexdigest() != image['sha256']:
            raise ValueError(f'Export image changed: {name}')
        files.append((source, 'images/' + name))
    return files


def _preview_file(row: dict) -> Path | None:
    if not row.get('problem_preview'):
        return None
    source = Path(row['problem_preview'])
    if source.is_symlink() or not source.is_file() or source.name != '未导出题目.html':
        raise ValueError('Question preview is missing or not a regular file')
    if hashlib.sha256(source.read_bytes()).hexdigest() != row.get('problem_preview_sha256'):
        raise ValueError('Question preview changed after generation')
    return source


def publish_single_delivery(summary: dict, work: Path) -> dict:
    """Expose the same customer layout for the single-document run CLI."""
    user = summary['user_result']
    row = {**user, 'name': summary['source'], 'total': summary['total'],
           'passed': summary['passed'], 'quarantined': summary['quarantined'],
           'export': str(work / 'export') if user['export'] else None}
    for field in ('export_preview', 'problem_preview'):
        if summary.get(field):
            row[field] = str(work / summary[field]['path'])
            row[field + '_sha256'] = summary[field]['sha256']
    row['display_assets'] = [{**asset, 'path': str(work / asset['path'])}
                             for asset in summary.get('display_assets', [])]
    return publish_delivery({'message': user['message'], 'total_files': 1,
                             'passed': summary['passed'] or 0,
                             'quarantined': summary['quarantined'] or 0,
                             'files': [row]}, work / '最终结果')


def publish_delivery(snapshot: dict, destination: Path) -> dict:
    """Create a new final-only directory, never overwrite an existing result.

    Returns delivery paths. A batch with no exported questions gets a report
    only. ZIP publication is atomic; a packaging error never leaves a partial
    archive bearing the final filename.
    """
    destination = Path(destination).expanduser().absolute()
    validate_delivery_names(row['name'] for row in snapshot['files'])
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f'Delivery directory already exists: {destination}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.qa-delivery-', dir=destination.parent))
    try:
        lines = [snapshot['message'], '',
                 f"文件数：{snapshot['total_files']}；已导出题数：{snapshot['passed']}；已确认未导出题数：{snapshot['quarantined']}。",
                 '无法读取的文件题数未知，不计入已确认未导出题数。', '']
        reports = []
        for index, row in enumerate(snapshot['files'], 1):
            name = f'_说明/{index:04d}'
            report = _document_report(row)
            reports.append((name, row, report))
            lines.extend([f'[{name}]', report])
        lines.extend(['请完整解压后使用。每份 Word 对应根目录同名 Excel 和同名图片文件夹；预览及说明位于 _说明/。',
                      '目标系统导入、图片关联和公式显示：未验证。'])
        report_text = '\n'.join(lines) + '\n'
        report_name = '批次结果说明.txt' if snapshot['passed'] else '转换失败说明.txt'
        (temporary / report_name).write_text(report_text, encoding='utf-8')
        archive_name = '题库最终结果.zip'
        if snapshot['passed']:
            with ZipFile(temporary / archive_name, 'x', compression=ZIP_DEFLATED) as archive:
                archive.writestr('批次结果说明.txt', report_text)
                shared = {}
                for name, row, report in reports:
                    stem = Path(row['name']).stem
                    archive.writestr(name + '/结果说明.txt', report)
                    preview_file = _preview_file(row)
                    if preview_file:
                        archive.write(preview_file, name + '/未导出题目.html')
                    if row.get('export'):
                        archive.writestr(stem + '/', b'')
                        for source, relative in _export_files(row):
                            target = (stem + '.xlsx' if relative == 'questions.xlsx' else
                                      stem + '/' + relative.removeprefix('images/'))
                            archive.write(source, target)
                        if row.get('export_preview'):
                            preview = Path(row['export_preview'])
                            if (preview.is_symlink() or not preview.is_file()
                                or preview.name != '题目预览.html'
                                or preview.resolve().parent != Path(row['export']).resolve()
                                or hashlib.sha256(preview.read_bytes()).hexdigest() != row.get('export_preview_sha256')):
                                raise ValueError('Export preview missing or changed')
                            # Relocate generated resource URLs, leaving formula
                            # text and CSP-hashed inline scripts unchanged.
                            html = preview.read_text(encoding='utf-8')
                            html = html.replace('src="images/', 'src="../../' + quote(stem, safe='') + '/')
                            html = html.replace('url(../_display/', 'url(../../_display/')
                            archive.writestr(name + '/题目预览.html', html)
                            assets = row.get('display_assets', [])
                            required = {'NotoSansCJKsc-Regular.otf', 'OFL.txt'}
                            if len(assets) != 2 or {Path(a['path']).name for a in assets} != required:
                                raise ValueError('Incomplete shared display resources')
                            for asset in assets:
                                source = Path(asset['path'])
                                if (source.is_symlink() or not source.is_file()
                                    or source.resolve().parent != preview.resolve().parent.parent / '_display'
                                    or hashlib.sha256(source.read_bytes()).hexdigest() != asset['sha256']):
                                    raise ValueError('Shared display resource missing or changed')
                                if source.name in shared and shared[source.name] != asset['sha256']:
                                    raise ValueError('Conflicting shared display resources')
                                if source.name not in shared:
                                    archive.write(source, '_display/' + source.name)
                                    shared[source.name] = asset['sha256']
            with ZipFile(temporary / archive_name) as archive:
                if archive.testzip() is not None:
                    raise ValueError('Delivery ZIP integrity check failed')
        else:
            # A failure-only run must not pretend to be a successful Excel ZIP.
            # Readable failed questions may still have a clearly labelled HTML.
            for name, row, report in reports:
                preview_file = _preview_file(row)
                if preview_file:
                    folder = temporary / name
                    folder.mkdir(parents=True)
                    shutil.copyfile(preview_file, folder / '未导出题目.html')
                    (folder / '结果说明.txt').write_text(report, encoding='utf-8')
        # Reserve only after all payloads have been verified; never replace an
        # existing directory created by another process while packaging.
        destination.mkdir()
        try:
            for source in temporary.iterdir():
                source.replace(destination / source.name)
        except BaseException:
            shutil.rmtree(destination)
            raise
        return {'delivery': str(destination / archive_name) if snapshot['passed'] else None,
                'delivery_report': str(destination / report_name),
                'delivery_directory': str(destination), 'delivery_status': 'success'}
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
