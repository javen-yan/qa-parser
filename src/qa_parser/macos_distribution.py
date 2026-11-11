"""macOS distribution and signed-update contracts.

The visible layout is a folder:

    题库转换.app        business application
    管理更新.app        independent update manager
    install-state.json  current/previous/pending versions

An update source is the ordinary distribution ZIP.  Every business build
contains ``Contents/Resources/update-manifest.json`` and an Ed25519 signature
over its canonical bytes.  The manifest lists every file and symlink in the app
except code-signing metadata and the manifest pair itself; ``codesign --verify``
covers the remaining bundle seal.  This keeps the release artifact count the
same as before while still giving the manager an offline, signed update.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import stat
import subprocess
import sys
from contextlib import contextmanager

PROTOCOL = 1
APP_NAME = '题库转换.app'
MANAGER_NAME = '管理更新.app'
APP_EXECUTABLE = 'Contents/MacOS/题库转换'
UPDATE_MANIFEST = 'update-manifest.json'
UPDATE_SIGNATURE = 'update-manifest.sig'
VERSION = re.compile(r'\d+\.\d+\.\d+')
TARGETS = {'arm64': 'macos-arm64', 'x86_64': 'macos-x86_64'}
ARCH_BY_TARGET = {value: key for key, value in TARGETS.items()}
UPDATE_SUFFIXES = ('.zip', '.qaupdate')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('wb') as stream:
        stream.write(canonical(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def safe_name(name, *, directory=False):
    """Return a normalized relative POSIX path or raise for unsafe input."""
    if not isinstance(name, str) or not name or len(name) > 220:
        raise ValueError('Invalid package path')
    value = name[:-1] if directory and name.endswith('/') else name
    parts = PurePosixPath(value).parts
    if (not parts or PurePosixPath(value).is_absolute() or '/'.join(parts) != value or
        any(part in ('.', '..') for part in parts) or
        any(part.endswith((' ', '.')) for part in parts) or
        any(c in name for c in '\\:\x00*?<>|"') or any(ord(c) < 32 for c in name)):
        raise ValueError('Unsafe package path')
    return value


def _excluded(relative):
    return relative == 'Contents/_CodeSignature' or relative.startswith('Contents/_CodeSignature/')


def app_inventory(app, *, excluded=()):
    """Hash physical files and preserve symlink targets without following links."""
    app = Path(app).absolute()
    if not app.is_dir() or app.is_symlink():
        raise ValueError('Application must be a real directory')
    root = app.resolve()
    result = {}
    for base, dirs, files in os.walk(app, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(base) / name
            relative = path.relative_to(app).as_posix()
            if _excluded(relative) or relative in excluded:
                continue
            metadata = path.lstat()
            if path.is_symlink():
                target = os.readlink(path)
                if Path(target).is_absolute():
                    raise ValueError(f'Absolute application symlink: {relative}')
                resolved = path.resolve(strict=True)
                if not resolved.is_relative_to(root):
                    raise ValueError(f'Application symlink escapes bundle: {relative}')
                result[relative] = {'kind': 'symlink', 'target': target}
            elif path.is_file():
                result[relative] = {'kind': 'file', 'bytes': metadata.st_size,
                                    'sha256': digest(path), 'mode': stat.S_IMODE(metadata.st_mode)}
            elif path.is_dir():
                continue
            else:
                raise ValueError(f'Unsupported application entry: {relative}')
    return result


def validate_manifest(data):
    if (data.get('schema_version') != 1 or data.get('target') not in ARCH_BY_TARGET or
        data.get('protocol') != PROTOCOL or not VERSION.fullmatch(str(data.get('version', '')))):
        raise ValueError('Unsupported macOS update manifest')
    files = data.get('files')
    if not isinstance(files, dict) or not files or len(files) > 20000:
        raise ValueError('Invalid application inventory')
    folded = set()
    for name, info in files.items():
        safe_name(name)
        if name.casefold() in folded or _excluded(name):
            raise ValueError('Duplicate or reserved application path')
        folded.add(name.casefold())
        if not isinstance(info, dict):
            raise ValueError('Invalid application entry')
        kind = info.get('kind')
        if kind == 'file':
            if (type(info.get('bytes')) is not int or info['bytes'] < 0 or
                not re.fullmatch('[0-9a-f]{64}', str(info.get('sha256', ''))) or
                type(info.get('mode')) is not int or not 0 <= info['mode'] <= 0o7777):
                raise ValueError('Invalid application file metadata')
        elif kind == 'symlink':
            target = info.get('target')
            if not isinstance(target, str) or not target or Path(target).is_absolute() or '\\' in target:
                raise ValueError('Invalid application symlink metadata')
        else:
            raise ValueError('Unsupported application entry kind')
    if APP_EXECUTABLE not in files or files[APP_EXECUTABLE].get('kind') != 'file':
        raise ValueError('Missing application executable')
    return data


def write_update_manifest(app, target, version, private_key, output):
    if target not in ARCH_BY_TARGET or not VERSION.fullmatch(version):
        raise ValueError('Invalid update manifest identity')
    data = {'schema_version': 1, 'target': target, 'version': version,
            'protocol': PROTOCOL, 'files': app_inventory(app)}
    raw = canonical(data)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / UPDATE_MANIFEST).write_bytes(raw)
    (output / UPDATE_SIGNATURE).write_bytes(private_key.sign(raw))
    return data


def read_update_manifest(manifest, public_key):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    manifest = Path(manifest)
    signature = manifest.with_name(UPDATE_SIGNATURE)
    raw = manifest.read_bytes()
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature.read_bytes(), raw)
    except (ValueError, InvalidSignature) as error:
        raise ValueError('更新包签名无效，未安装。') from error
    data = json.loads(raw)
    validate_manifest(data)
    if canonical(data) != raw:
        raise ValueError('Non-canonical update manifest')
    return data


def plist_version(app):
    import plistlib
    with (Path(app) / 'Contents/Info.plist').open('rb') as stream:
        plist = plistlib.load(stream)
    return str(plist.get('CFBundleShortVersionString') or plist.get('CFBundleVersion') or '')


def app_architecture(app):
    executable = Path(app) / APP_EXECUTABLE
    if not executable.is_file():
        raise ValueError('Application executable missing')
    output = subprocess.check_output(['/usr/bin/lipo', '-archs', str(executable)], text=True, timeout=20).split()
    return output


def verify_codesign(app):
    if sys.platform != 'darwin':
        raise ValueError('macOS code-signature verification requires macOS')
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)],
                   text=True, timeout=60, check=True)


def verify_app(app, manifest, public_key, *, target=None, check_codesign=True, check_arch=True):
    data = read_update_manifest(manifest, public_key)
    if target is not None and data['target'] != target:
        raise ValueError('更新包平台或架构不匹配。')
    if plist_version(app) != data['version']:
        raise ValueError('Application version differs from update manifest')
    if app_inventory(app) != data['files']:
        raise ValueError('Application files differ from update manifest')
    if target is not None and check_arch:
        architectures = app_architecture(app)
        if ARCH_BY_TARGET[target] not in architectures:
            raise ValueError('更新包原生架构不匹配。')
    if check_codesign:
        verify_codesign(app)
    return data


def host_target():
    machine = platform.machine()
    if machine not in TARGETS:
        raise ValueError(f'Unsupported macOS architecture: {machine}')
    return TARGETS[machine]


def root_for_app(executable=None):
    path = Path(executable or sys.executable).resolve()
    # .../题库转换.app/Contents/MacOS/题库转换 -> the folder containing the app
    if len(path.parents) < 4 or path.parents[2].name != APP_NAME:
        raise ValueError('Application is not inside a managed distribution folder')
    return path.parents[3]


def root_for_manager(executable=None):
    path = Path(executable or sys.executable).resolve()
    # .../管理更新.app/Contents/MacOS/管理更新 -> the folder containing the app
    if len(path.parents) < 4 or path.parents[2].name != MANAGER_NAME:
        raise ValueError('管理器不在完整程序文件夹内')
    return path.parents[3]


def read_state(root):
    path = Path(root) / 'install-state.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise ValueError('安装状态缺失，请使用完整程序包。') from None
    if data.get('schema_version') != 1 or not VERSION.fullmatch(str(data.get('current', ''))):
        raise ValueError('Invalid installation state')
    for name in ('previous', 'pending', 'fallback_previous'):
        if data.get(name) is not None and not VERSION.fullmatch(str(data[name])):
            raise ValueError('Invalid installation state')
    return data


def write_state(root, current, previous, pending=None, fallback_previous=None):
    atomic_json(Path(root) / 'install-state.json', {
        'schema_version': 1, 'current': current, 'previous': previous,
        'pending': pending, 'fallback_previous': fallback_previous})


@contextmanager
def file_lock(root, name, shared=False):
    """Non-blocking activity/operation lock; released by the OS on exit."""
    if os.name == 'nt':
        raise RuntimeError('macOS locks are not available on Windows')
    import fcntl
    path = Path(root) / ('.' + name + '.lock')
    with path.open('a+b') as stream:
        try:
            fcntl.flock(stream, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError('程序或更新正在运行，请关闭全部窗口并等待转换结束。') from error
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
