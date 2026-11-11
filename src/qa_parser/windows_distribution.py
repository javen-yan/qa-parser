"""Windows split-layout contracts. Importing this module never changes other platforms."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
from contextlib import contextmanager

PROTOCOL = 1
TARGET = 'windows-x64'
APP_EXE = '题库转换.exe'
VERSION = re.compile(r'1\.(\d+)\.(\d+)')
RUNTIME = re.compile(r'r1-[0-9a-f]{64}')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_name(name):
    if not isinstance(name, str) or not name or len(name) > 220:
        raise ValueError('Invalid package path')
    parts = PurePosixPath(name).parts
    if (PurePosixPath(name).is_absolute() or '/'.join(parts) != name or
        any(c in name for c in '\\:\x00*?<>|"') or any(ord(c) < 32 for c in name)):
        raise ValueError('Unsafe package path')
    for part in parts:
        if part in ('.', '..') or part.endswith((' ', '.')) or re.match(r'(?i)^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\.|$)', part):
            raise ValueError('Unsafe Windows package path')
    return name


def inventory(root, exclude=()):
    result = {}
    for path in sorted(Path(root).rglob('*')):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('Links are not allowed in packages')
        if path.is_file():
            name = safe_name(path.relative_to(root).as_posix())
            if name not in exclude:
                result[name] = {'bytes': path.stat().st_size, 'sha256': digest(path)}
    return result


def check_files(root, expected, exclude=()):
    if inventory(root, exclude) != expected:
        raise ValueError('Package files are missing, unexpected or damaged')


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('wb') as stream:
        stream.write(canonical(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def split_app(executable=None):
    if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
        return None
    app = Path(executable or sys.executable).resolve().parent
    # Once inside the split tree, missing metadata is an error, never a fallback.
    if app.parent.name != 'app':
        return None
    data = json.loads((app / 'app-manifest.json').read_text(encoding='utf-8'))
    validate_manifest(data)
    if app.name != 'v' + data['version']:
        raise ValueError('Business directory/version mismatch')
    return app, data


def validate_manifest(data):
    if (data.get('schema_version') != 1 or data.get('target') != TARGET or
        data.get('launcher_protocol') != PROTOCOL or
        not VERSION.fullmatch(str(data.get('version', ''))) or
        not RUNTIME.fullmatch(str(data.get('runtime_id', '')))):
        raise ValueError('Unsupported business package or runtime; install the full package')
    files = data.get('files')
    if not isinstance(files, dict) or not files or len(files) > 20000:
        raise ValueError('Invalid file inventory')
    folded = set()
    for name, info in files.items():
        safe_name(name)
        if name.casefold() in folded or name in ('app-manifest.json', 'app-manifest.sig'):
            raise ValueError('Duplicate/reserved package path')
        folded.add(name.casefold())
        if (not isinstance(info, dict) or type(info.get('bytes')) is not int or info['bytes'] < 0 or
            not re.fullmatch('[0-9a-f]{64}', str(info.get('sha256', '')))):
            raise ValueError('Invalid file metadata')
    if APP_EXE not in files or sum(i['bytes'] for i in files.values()) > 2 * 1024**3:
        raise ValueError('Missing executable or oversized package')


def runtime_root(app, manifest, verify=True):
    root = app.parent.parent / 'runtime' / manifest['runtime_id']
    if root.is_symlink() or (hasattr(root, 'is_junction') and root.is_junction()):
        raise ValueError('Runtime directory cannot be a link')
    info = json.loads((root / 'runtime-manifest.json').read_text(encoding='utf-8'))
    if 'r1-' + hashlib.sha256(canonical(info)).hexdigest() != manifest['runtime_id']:
        raise ValueError('Runtime identity mismatch; install the full package')
    if verify:
        check_files(root, info['files'], ('runtime-manifest.json',))
    return root


def resource_root():
    split = split_app()
    if split:
        return runtime_root(*split, verify=False)
    return Path(sys._MEIPASS)


@contextmanager
def file_lock(root, name, shared=False):
    """OS-held locks survive no crashes; never infer liveness from stale PID files."""
    path = Path(root) / ('.' + name + '.lock')
    with path.open('a+b') as stream:
        if path.stat().st_size == 0:
            stream.write(b'\0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBRLCK if shared else msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError('程序或更新正在运行，请关闭全部窗口并等待转换结束。') from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)
