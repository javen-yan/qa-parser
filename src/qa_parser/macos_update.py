"""Signed macOS offline update transactions.

The manager owns the operation lock and takes an exclusive activity lock while
files are swapped.  The running application holds a shared activity lock, so a
bundle is never replaced underneath a live process.
"""
from __future__ import annotations

import json
from pathlib import Path
import os
import shutil
import stat
from zipfile import ZipFile

from .macos_distribution import (APP_NAME, UPDATE_MANIFEST, UPDATE_SIGNATURE,
                                 canonical, host_target, read_state, safe_name,
                                 validate_manifest, verify_app, write_state)


def verify_manifest(raw, signature, public_key):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, raw)
    except (ValueError, InvalidSignature) as error:
        raise ValueError('更新包发布签名无效，未安装。') from error
    data = json.loads(raw)
    validate_manifest(data)
    if canonical(data) != raw:
        raise ValueError('Non-canonical manifest')
    return data


def order(version):
    return tuple(map(int, version.split('.')))


def _inside(path, root):
    try:
        return Path(os.path.realpath(path)).is_relative_to(Path(os.path.realpath(root)))
    except OSError:
        return False


def _locate_app(staging):
    root = Path(staging)
    direct = root / APP_NAME
    if direct.is_dir() and not direct.is_symlink():
        return direct
    children = [p for p in root.iterdir() if not p.name.startswith('.')]
    if len(children) == 1 and (children[0] / APP_NAME).is_dir() and not (children[0] / APP_NAME).is_symlink():
        return children[0] / APP_NAME
    raise ValueError('更新包中未找到题库转换.app')


def _zip_name(item):
    """Decode ditto/ZIP names that carry UTF-8 bytes without the UTF-8 flag."""
    name = item.filename
    if item.flag_bits & 0x800:
        return name
    try:
        candidate = name.encode('cp437').decode('utf-8')
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name
    return candidate or name


def _extract(archive, staging):
    staging = Path(staging)
    staging.mkdir()
    root = staging.resolve()
    with ZipFile(archive) as z:
        entries = z.infolist()
        if len(entries) > 20000:
            raise ValueError('更新包文件数量异常')
        folded = set()
        total = 0
        for item in entries:
            decoded = _zip_name(item)
            name = safe_name(decoded, directory=item.is_dir())
            if decoded.casefold() in folded:
                raise ValueError('更新包包含重复路径')
            folded.add(decoded.casefold())
            if any(part == '__MACOSX' or part.startswith('._') for part in Path(name).parts):
                raise ValueError('更新包包含资源叉元数据')
            if item.flag_bits & 1:
                raise ValueError('更新包不得加密')
            mode = item.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if item.is_dir():
                if kind not in (0, stat.S_IFDIR):
                    raise ValueError('更新包目录类型异常')
                (staging / name).mkdir(parents=True, exist_ok=True)
            elif stat.S_ISLNK(mode):
                # Symlinks are created after regular files, never followed.
                continue
            elif kind in (0, stat.S_IFREG):
                total += item.file_size
                if total > 2 * 1024**3:
                    raise ValueError('更新包解压后体积异常')
                target = staging / name
                if not _inside(target.parent, root):
                    raise ValueError('更新包路径越界')
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(item) as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                os.chmod(target, stat.S_IMODE(mode) or 0o644)
            else:
                raise ValueError('更新包不得包含特殊文件')
        # Create validated relative symlinks only after all regular files exist.
        for item in entries:
            mode = item.external_attr >> 16
            if not stat.S_ISLNK(mode):
                continue
            name = safe_name(_zip_name(item))
            target_text = z.read(item).decode('utf-8')
            if not target_text or Path(target_text).is_absolute() or '\\' in target_text:
                raise ValueError('更新包符号链接目标非法')
            link = staging / name
            if not _inside(link.parent, root) or not _inside(link.parent / target_text, root):
                raise ValueError('更新包符号链接越界')
            link.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(target_text, link)
    return staging


def _remove_staging(root):
    stage = Path(root) / '.update-staging'
    if stage.exists():
        if stage.is_symlink() or (hasattr(stage, 'is_junction') and stage.is_junction()):
            raise ValueError('Unsafe update staging directory')
        shutil.rmtree(stage)


def _version_dir(root, version):
    return Path(root) / '.versions' / ('v' + version)


def _stored(root, version):
    return _version_dir(root, version) / APP_NAME


def _stored_manifest(root, version):
    return _version_dir(root, version) / UPDATE_MANIFEST


def _remove_version(root, version):
    path = _version_dir(root, version)
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _move_to_store(root, version):
    """Move the active app and its sidecar manifest into .versions/<version>."""
    for name in (APP_NAME, UPDATE_MANIFEST, UPDATE_SIGNATURE):
        if not (Path(root) / name).exists():
            raise ValueError('当前版本文件不完整，请使用完整程序包。')
    target = _version_dir(root, version)
    _remove_version(root, version)
    target.mkdir(parents=True)
    os.replace(Path(root) / APP_NAME, target / APP_NAME)
    os.replace(Path(root) / UPDATE_MANIFEST, target / UPDATE_MANIFEST)
    os.replace(Path(root) / UPDATE_SIGNATURE, target / UPDATE_SIGNATURE)
    return target


def _move_from_store(root, version):
    """Restore a version and its sidecar manifest to the distribution root."""
    source = _version_dir(root, version)
    if not (source / APP_NAME).is_dir() or not (source / UPDATE_MANIFEST).is_file():
        raise ValueError('上一版本文件缺失，无法恢复。')
    os.replace(source / APP_NAME, Path(root) / APP_NAME)
    os.replace(source / UPDATE_MANIFEST, Path(root) / UPDATE_MANIFEST)
    os.replace(source / UPDATE_SIGNATURE, Path(root) / UPDATE_SIGNATURE)
    try:
        source.rmdir()
    except OSError:
        pass


def _restore_current(root, version):
    if not (Path(root) / APP_NAME).exists() and _stored(root, version).exists():
        _move_from_store(root, version)


def _swap_to_previous(root):
    state = read_state(root)
    previous = state.get('previous')
    if not previous or previous == state['current']:
        raise ValueError('没有可恢复的上一版本。')
    if not _stored(root, previous).is_dir() or not _stored_manifest(root, previous).is_file():
        raise ValueError('上一版本文件缺失，无法恢复。')
    try:
        _move_to_store(root, state['current'])
        _move_from_store(root, previous)
    except Exception:
        _restore_current(root, state['current'])
        raise
    write_state(root, previous, state['current'], pending=None)
    return read_state(root)


def recover(root):
    """Caller owns the operation and exclusive activity locks."""
    state = read_state(root)
    if state.get('pending'):
        state = _swap_to_previous(root)
    _remove_staging(root)
    return state


def stage_update(root, archive, public_key, *, target=None, check_codesign=True, check_arch=True):
    """Extract, verify and atomically activate a signed distribution archive.

    Must hold the operation and exclusive activity locks.  The new version is
    marked pending until the application acknowledges a healthy window.
    """
    root = Path(root)
    state = recover(root)
    target = target or host_target()
    archive = Path(archive)
    if archive.suffix.lower() not in ('.zip', '.qaupdate'):
        raise ValueError('请选择 .zip 或 .qaupdate 离线更新包。')
    stage = root / '.update-staging'
    _extract(archive, stage)
    app = _locate_app(stage)
    manifest = app.parent / UPDATE_MANIFEST
    data = verify_app(app, manifest, public_key, target=target, check_codesign=check_codesign, check_arch=check_arch)
    if order(data['version']) <= order(state['current']):
        raise ValueError('请选择更新版本；降级请使用“恢复上一版本”。')
    current_app = root / APP_NAME
    if not current_app.is_dir() or current_app.is_symlink():
        raise ValueError('当前程序目录缺失，请使用完整程序包。')
    _move_to_store(root, state['current'])
    try:
        os.replace(manifest, root / UPDATE_MANIFEST)
        os.replace(manifest.with_name(UPDATE_SIGNATURE), root / UPDATE_SIGNATURE)
        os.replace(app, current_app)
    except Exception:
        _restore_current(root, state['current'])
        raise
    write_state(root, data['version'], state['current'],
                pending=data['version'], fallback_previous=state.get('previous'))
    _remove_staging(root)
    return data


def rollback(root, public_key, *, check_codesign=True, check_arch=True):
    """Switch back to the previous version and keep the current one recoverable."""
    root = Path(root)
    state = recover(root)
    previous = state.get('previous')
    if not previous or previous == state['current']:
        raise ValueError('没有可恢复的上一版本。')
    previous_app = _stored(root, previous)
    if not previous_app.is_dir() or previous_app.is_symlink():
        raise ValueError('上一版本文件缺失，无法恢复。')
    verify_app(previous_app, _stored_manifest(root, previous), public_key,
               check_codesign=check_codesign, check_arch=check_arch)
    try:
        _move_to_store(root, state['current'])
        _move_from_store(root, previous)
    except Exception:
        _restore_current(root, state['current'])
        raise
    write_state(root, previous, state['current'], pending=previous,
                fallback_previous=state.get('previous'))
    return read_state(root)


def finish_start(root, version, success):
    state = read_state(root)
    if state.get('pending') != version:
        return state
    if success:
        write_state(root, state['current'], state.get('previous'))
        return read_state(root)
    return _swap_to_previous(root)
