"""Signed offline update transactions; no networking, no dependency installation."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import stat
from zipfile import ZipFile

from .windows_distribution import (APP_EXE, VERSION, canonical, check_files, atomic_json,
                                   safe_name, validate_manifest, runtime_root)

METADATA = ('app-manifest.json', 'app-manifest.sig')


def verify_manifest(raw, signature, public_key):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, raw)
    except (ValueError, InvalidSignature) as error:
        raise ValueError('更新包发布签名无效，未安装。') from error
    data = json.loads(raw)
    validate_manifest(data)
    if canonical(data) != raw:
        raise ValueError('Non-canonical manifest')
    return data


def installed(root, version, public_key):
    if not VERSION.fullmatch(version):
        raise ValueError('Invalid installed version')
    app = root / 'app' / ('v' + version)
    if app.is_symlink() or (hasattr(app, 'is_junction') and app.is_junction()):
        raise ValueError('Application directory cannot be a link')
    data = verify_manifest((app / METADATA[0]).read_bytes(), (app / METADATA[1]).read_bytes(), public_key)
    if data['version'] != version:
        raise ValueError('Installed version mismatch')
    check_files(app, data['files'], METADATA)
    runtime_root(app, data)
    return app, data


def read_state(root):
    data = json.loads((root / 'install-state.json').read_text(encoding='utf-8'))
    if data.get('schema_version') != 1 or not VERSION.fullmatch(str(data.get('current', ''))):
        raise ValueError('Invalid installation state')
    for name in ('previous', 'pending'):
        if data.get(name) is not None and not VERSION.fullmatch(str(data[name])):
            raise ValueError('Invalid installation state')
    return data


def recover(root):
    """Caller owns operation + exclusive activity locks. No application files deleted."""
    state = read_state(root)
    if state.get('pending'):
        # Interrupted before readiness acknowledgement: restore the old pointer.
        state['current'], state['pending'] = state['previous'], None
        if not state['current']:
            raise ValueError('Interrupted update has no recovery version')
        atomic_json(root / 'install-state.json', state)
    stage = root / '.update-staging'
    if stage.exists():
        if stage.is_symlink() or (hasattr(stage, 'is_junction') and stage.is_junction()):
            raise ValueError('Unsafe update staging directory')
        shutil.rmtree(stage)
    return state


def stage_update(root, archive, public_key):
    """Must hold operation + exclusive activity locks for the entire transaction."""
    state = recover(root)
    stage = root / '.update-staging'
    with ZipFile(archive) as z:
        entries = z.infolist()
        names = [i.filename for i in entries]
        if len(names) != len(set(n.casefold() for n in names)) or len(names) > 20002:
            raise ValueError('Duplicate or excessive ZIP entries')
        for item in entries:
            safe_name(item.filename)
            mode = item.external_attr >> 16
            if item.is_dir() or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG)) or item.flag_bits & 1:
                raise ValueError('Links, special files, directories and encryption are forbidden')
        if not set(METADATA).issubset(names) or z.getinfo(METADATA[0]).file_size > 8 * 1024**2 or z.getinfo(METADATA[1]).file_size != 64:
            raise ValueError('Missing or oversized update metadata')
        manifest = verify_manifest(z.read(METADATA[0]), z.read(METADATA[1]), public_key)
        if set(names) != set(manifest['files']) | set(METADATA):
            raise ValueError('Unexpected or missing package entries')
        def order(v): return tuple(map(int, v.split('.')))
        if order(manifest['version']) <= order(state['current']):
            raise ValueError('请选择更新版本；降级请使用“恢复上一版本”。')
        destination = root / 'app' / ('v' + manifest['version'])
        runtime_root(destination, manifest)  # fail before unpacking or switching
        stage.mkdir()
        try:
            for item in entries:
                if item.filename in manifest['files'] and item.file_size != manifest['files'][item.filename]['bytes']:
                    raise ValueError('ZIP size mismatch')
                target = stage / item.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(item) as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
            check_files(stage, manifest['files'], METADATA)
            if destination.exists():
                old = installed(root, manifest['version'], public_key)[1]
                if old != manifest:
                    raise ValueError('Version already exists with different contents')
                shutil.rmtree(stage)
            else:
                stage.rename(destination)
            atomic_json(root / 'install-state.json', {
                'schema_version': 1, 'current': manifest['version'],
                'previous': state['current'], 'pending': manifest['version']})
        except Exception:
            if stage.exists():
                shutil.rmtree(stage)
            raise
    return manifest


def rollback(root, public_key):
    state = recover(root)
    previous = state.get('previous')
    if not previous or previous == state['current']:
        raise ValueError('没有可恢复的上一版本。')
    installed(root, previous, public_key)
    atomic_json(root / 'install-state.json', {
        'schema_version': 1, 'current': previous, 'previous': state['current'], 'pending': previous})


def finish_start(root, version, success):
    state = read_state(root)
    if state.get('pending') == version:
        if not success:
            state['current'] = state['previous']
        state['pending'] = None
        atomic_json(root / 'install-state.json', state)
