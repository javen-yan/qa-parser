"""Signed package and interrupted-update contracts; synthetic data, no network."""
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
from zipfile import ZipFile, ZipInfo

import pytest

from qa_parser.windows_distribution import canonical, inventory, atomic_json, file_lock
from qa_parser.windows_update import stage_update, installed, recover, finish_start, rollback


def builder():
    spec = importlib.util.spec_from_file_location('split_builder', Path(__file__).resolve().parents[1] / 'scripts/build_windows_distribution.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def installation(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    b = builder()
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes_raw()
    root = tmp_path / '中文 安装'
    runtime = root / 'runtime' / 'staging'
    runtime.mkdir(parents=True)
    (runtime / 'runtime.bin').write_bytes(b'fixed runtime')
    info = {'schema_version': 1, 'target': 'windows-x64', 'files': inventory(runtime)}
    rid = 'r1-' + hashlib.sha256(canonical(info)).hexdigest()
    atomic_json(runtime / 'runtime-manifest.json', info)
    runtime.rename(runtime.with_name(rid))
    app = root / 'app/v1.0.0'
    app.mkdir(parents=True)
    (app / '题库转换.exe').write_bytes(b'old app')
    b.write_business_manifest(app, '1.0.0', rid, key)
    atomic_json(root / 'install-state.json', {'schema_version': 1, 'current': '1.0.0', 'previous': None, 'pending': None})
    newer = tmp_path / 'new'
    newer.mkdir()
    (newer / '题库转换.exe').write_bytes(b'new app')
    b.write_business_manifest(newer, '1.4.2', rid, key)
    archive = tmp_path / 'new.qaupdate'
    b.archive_tree(newer, archive)
    return root, archive, public, key, newer, rid


def test_cross_version_update_and_rollback_preserve_runtime_and_customer_data(installation):
    root, archive, public, *_ = installation
    customer = root.parent / 'customer.docx'
    customer.write_bytes(b'untouched')
    before = inventory(root / 'runtime')
    with file_lock(root, 'operation'), file_lock(root, 'activity'):
        stage_update(root, archive, public)
        assert installed(root, '1.4.2', public)[0].is_dir()
        finish_start(root, '1.4.2', True)
        rollback(root, public)
        finish_start(root, '1.0.0', True)
    assert json.loads((root / 'install-state.json').read_text())['current'] == '1.0.0'
    assert before == inventory(root / 'runtime')
    assert customer.read_bytes() == b'untouched'


@pytest.mark.parametrize('complete', [False, True])
def test_interrupted_switch_and_startup_failure_restore_previous(installation, complete):
    root, archive, public, *_ = installation
    stage_update(root, archive, public)
    if complete:
        finish_start(root, '1.4.2', False)
    else:
        recover(root)
    assert json.loads((root / 'install-state.json').read_text())['current'] == '1.0.0'
    # Retrying an already fully staged identical version is safe.
    stage_update(root, archive, public)
    finish_start(root, '1.4.2', True)
    assert recover(root)['current'] == '1.4.2'


@pytest.mark.parametrize('mutation', ['signature', 'content', 'extra', 'traversal', 'case', 'symlink', 'runtime', 'platform', 'protocol', 'downgrade'])
def test_reject_bad_packages_before_switch(installation, mutation):
    root, archive, public, key, new, rid = installation
    original = (root / 'install-state.json').read_bytes()
    with ZipFile(archive) as z:
        contents = {i.filename: z.read(i) for i in z.infolist()}
    if mutation == 'signature':
        contents['app-manifest.sig'] = b'x' * 64
    elif mutation == 'content':
        contents['题库转换.exe'] = b'tampered'
    elif mutation in ('extra', 'traversal', 'case'):
        contents[{'extra':'extra.txt', 'traversal':'../escape', 'case':'APP-MANIFEST.JSON'}[mutation]] = b'x'
    elif mutation in ('runtime', 'platform', 'protocol', 'downgrade'):
        data = json.loads(contents['app-manifest.json'])
        name, value = {'runtime':('runtime_id', 'r1-' + '0'*64), 'platform':('target','macos-arm64'),
                       'protocol':('launcher_protocol',2), 'downgrade':('version','1.0.0')}[mutation]
        data[name] = value
        contents['app-manifest.json'] = canonical(data)
        contents['app-manifest.sig'] = key.sign(canonical(data))
    with ZipFile(archive, 'w') as z:
        for name, data in contents.items():
            if mutation == 'symlink' and name == '题库转换.exe':
                entry = ZipInfo(name)
                entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                z.writestr(entry, data)
            else:
                z.writestr(name, data)
    with pytest.raises((ValueError, FileNotFoundError)):
        stage_update(root, archive, public)
    assert (root / 'install-state.json').read_bytes() == original
    assert not (root / 'app/v1.4.2').exists()


def test_activity_lock_blocks_update_while_process_alive(installation):
    root = installation[0]
    with file_lock(root, 'activity', shared=True):
        with pytest.raises(RuntimeError, match='正在运行'):
            with file_lock(root, 'activity'):
                pytest.fail('exclusive lock acquired while business process is active')
    with file_lock(root, 'activity'):
        pass


def test_runtime_tamper_is_rejected(installation):
    root, archive, public, _, _, rid = installation
    (root / 'runtime' / rid / 'runtime.bin').write_bytes(b'damaged')
    with pytest.raises(ValueError, match='damaged'):
        stage_update(root, archive, public)


def test_incomplete_staging_cleanup_does_not_remove_versions(installation):
    root = installation[0]
    stage = root / '.update-staging'
    stage.mkdir()
    (stage / 'partial').write_bytes(b'incomplete')
    recover(root)
    assert not stage.exists()
    assert (root / 'app/v1.0.0/题库转换.exe').exists()


def test_runtime_identity_independent_of_business_python_licenses(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    b = builder()
    key = Ed25519PrivateKey.generate()
    source = tmp_path / 'source'
    for name in ('webview2', 'wmf', 'licenses/python', 'licenses/sources'):
        folder = source / '_internal' / name
        folder.mkdir(parents=True)
        (folder / 'file').write_bytes(name.encode())
    (source / '题库转换.exe').write_bytes(b'business')
    atomic_json(source / '_internal/resources-manifest.json', {'files': [
        {'path': 'licenses/python/file', 'bytes': 5}, {'path': 'wmf/file', 'bytes': 3}]})
    first = b.split(source, tmp_path / 'first', '1.0.0', key)
    (source / '_internal/licenses/python/file').write_bytes(b'new Python dependencies')
    atomic_json(source / '_internal/resources-manifest.json', {'files': [
        {'path': 'licenses/python/file', 'bytes': 23}, {'path': 'wmf/file', 'bytes': 3}]})
    second = b.split(source, tmp_path / 'second', '1.1.0', key)
    assert first['runtime_id'] == second['runtime_id']
    assert (tmp_path / 'second/app/v1.1.0/_internal/licenses/python/file').exists()
    assert not (tmp_path / 'second/app/v1.1.0/_internal/webview2').exists()
    assert not (tmp_path / 'second/runtime' / second['runtime_id'] / 'licenses/python').exists()


def test_failed_upgrade_keeps_existing_rollback_history(installation):
    root, archive, public, key, newer, rid = installation
    stage_update(root, archive, public)
    finish_start(root, '1.4.2', True)
    builder().write_business_manifest(newer, '1.5.0', rid, key)
    next_update = archive.with_name('next.qaupdate')
    builder().archive_tree(newer, next_update)
    stage_update(root, next_update, public)
    finish_start(root, '1.5.0', False)
    state = json.loads((root / 'install-state.json').read_text())
    assert (state['current'], state['previous']) == ('1.4.2', '1.0.0')
    rollback(root, public)
    finish_start(root, '1.0.0', True)
    assert json.loads((root / 'install-state.json').read_text())['current'] == '1.0.0'
