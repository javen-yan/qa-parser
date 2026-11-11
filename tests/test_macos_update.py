import json
import plistlib
import zipfile
from pathlib import Path
import stat

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from qa_parser.macos_distribution import (APP_NAME, APP_EXECUTABLE, UPDATE_MANIFEST, UPDATE_SIGNATURE,
                                          app_inventory, canonical, read_state,
                                          safe_name, verify_app, write_state,
                                          write_update_manifest)
from qa_parser.macos_update import finish_start
from qa_parser.macos_update import recover, rollback, stage_update


def keys():
    key = Ed25519PrivateKey.generate()
    return key, key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def fake_app(parent, version):
    app = Path(parent) / APP_NAME
    (app / 'Contents/MacOS').mkdir(parents=True)
    (app / 'Contents/Resources').mkdir(parents=True)
    with (app / 'Contents/Info.plist').open('wb') as stream:
        plistlib.dump({'CFBundleShortVersionString': version, 'CFBundleVersion': version}, stream)
    executable = app / APP_EXECUTABLE
    executable.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
    executable.chmod(0o755)
    (app / 'Contents/Resources/data.txt').write_text('data ' + version, encoding='utf-8')
    (app / 'Contents/Frameworks').mkdir()
    (app / 'Contents/Frameworks/link').symlink_to('../Resources/data.txt')
    return app


def archive_app(app, destination, manifest_root):
    destination = Path(destination)
    manifest_root = Path(manifest_root)
    with zipfile.ZipFile(destination, 'x', zipfile.ZIP_DEFLATED) as z:
        for name in (UPDATE_MANIFEST, UPDATE_SIGNATURE):
            path = manifest_root / name
            info = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, path.read_bytes())
        for path in sorted(Path(app).rglob('*')):
            relative = (Path(APP_NAME) / path.relative_to(app)).as_posix()
            if path.is_symlink():
                info = zipfile.ZipInfo(relative, (2020, 1, 1, 0, 0, 0))
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                z.writestr(info, str(path.readlink()))
            elif path.is_file():
                info = zipfile.ZipInfo(relative, (2020, 1, 1, 0, 0, 0))
                info.external_attr = (stat.S_IFREG | stat.S_IMODE(path.stat().st_mode)) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, path.read_bytes())
    return destination


def installed_root(tmp_path, version='1.0.0'):
    root = tmp_path / 'installed'
    root.mkdir()
    app = fake_app(root, version)
    return root, app


def test_manifest_covers_files_and_symlinks_but_not_signature_metadata(tmp_path):
    key, public = keys()
    app = fake_app(tmp_path, '1.0.1')
    data = write_update_manifest(app, 'macos-arm64', '1.0.1', key, tmp_path)
    assert data['target'] == 'macos-arm64'
    assert 'Contents/Resources/data.txt' in data['files']
    assert data['files']['Contents/Frameworks/link'] == {'kind': 'symlink', 'target': '../Resources/data.txt'}
    assert UPDATE_MANIFEST not in data['files']
    assert 'Contents/Resources/update-manifest.sig' not in data['files']
    (app / 'Contents/_CodeSignature').mkdir()
    (app / 'Contents/_CodeSignature/CodeResources').write_text('signature', encoding='utf-8')
    assert app_inventory(app) == data['files']
    assert verify_app(app, tmp_path / UPDATE_MANIFEST, public, target='macos-arm64', check_codesign=False, check_arch=False)['version'] == '1.0.1'


def test_update_manifest_rejects_tampering(tmp_path):
    key, public = keys()
    app = fake_app(tmp_path, '1.0.1')
    write_update_manifest(app, 'macos-arm64', '1.0.1', key, tmp_path)
    (app / 'Contents/Resources/data.txt').write_text('tampered', encoding='utf-8')
    with pytest.raises(ValueError, match='differ'):
        verify_app(app, tmp_path / UPDATE_MANIFEST, public, target='macos-arm64', check_codesign=False, check_arch=False)


def test_safe_name_rejects_traversal():
    with pytest.raises(ValueError):
        safe_name('../escape')
    with pytest.raises(ValueError):
        safe_name('/absolute')
    with pytest.raises(ValueError):
        safe_name('a/../../b')
    assert safe_name('题库转换.app/Contents/Info.plist') == '题库转换.app/Contents/Info.plist'


def test_signed_upgrade_and_rollback(tmp_path):
    key, public = keys()
    root, current = installed_root(tmp_path)
    write_update_manifest(current, 'macos-arm64', '1.0.0', key, root)
    write_state(root, '1.0.0', None)
    new = fake_app(tmp_path / 'new', '1.0.1')
    write_update_manifest(new, 'macos-arm64', '1.0.1', key, tmp_path / 'new')
    package = archive_app(new, tmp_path / 'update.zip', tmp_path / 'new')
    stage_update(root, package, public, target='macos-arm64', check_codesign=False, check_arch=False)
    state = read_state(root)
    assert (state['current'], state['previous'], state['pending']) == ('1.0.1', '1.0.0', '1.0.1')
    assert (root / APP_NAME / 'Contents/Resources/data.txt').read_text() == 'data 1.0.1'
    finish_start(root, '1.0.1', True)
    assert read_state(root)['pending'] is None
    rollback(root, public, check_codesign=False, check_arch=False)
    state = read_state(root)
    assert (state['current'], state['previous'], state['pending']) == ('1.0.0', '1.0.1', '1.0.0')
    assert (root / APP_NAME / 'Contents/Resources/data.txt').read_text() == 'data 1.0.0'


def test_failed_startup_restores_previous_app(tmp_path):
    key, public = keys()
    root, current = installed_root(tmp_path)
    write_update_manifest(current, 'macos-arm64', '1.0.0', key, root)
    write_state(root, '1.0.0', None)
    new = fake_app(tmp_path / 'new', '1.0.1')
    write_update_manifest(new, 'macos-arm64', '1.0.1', key, tmp_path / 'new')
    package = archive_app(new, tmp_path / 'update.zip', tmp_path / 'new')
    stage_update(root, package, public, target='macos-arm64', check_codesign=False, check_arch=False)
    finish_start(root, '1.0.1', False)
    state = read_state(root)
    assert state['current'] == '1.0.0'
    assert state['pending'] is None
    assert (root / APP_NAME / 'Contents/Resources/data.txt').read_text() == 'data 1.0.0'
    assert not (root / '.update-staging').exists()


def test_recover_removes_interrupted_staging(tmp_path):
    key, public = keys()
    root, current = installed_root(tmp_path)
    write_update_manifest(current, 'macos-arm64', '1.0.0', key, root)
    write_state(root, '1.0.0', None)
    (root / '.update-staging').mkdir()
    (root / '.update-staging/partial').write_text('partial', encoding='utf-8')
    recover(root)
    assert not (root / '.update-staging').exists()
    assert read_state(root)['current'] == '1.0.0'


def test_rejects_downgrade_and_wrong_target(tmp_path):
    key, public = keys()
    root, current = installed_root(tmp_path)
    write_update_manifest(current, 'macos-arm64', '1.0.0', key, root)
    write_state(root, '1.0.0', None)
    new = fake_app(tmp_path / 'new', '1.0.1')
    write_update_manifest(new, 'macos-arm64', '1.0.1', key, tmp_path / 'new')
    package = archive_app(new, tmp_path / 'update.zip', tmp_path / 'new')
    with pytest.raises(ValueError, match='平台或架构'):
        stage_update(root, package, public, target='macos-x86_64', check_codesign=False)
    write_state(root, '1.0.2', '1.0.0')
    with pytest.raises(ValueError, match='更新版本'):
        stage_update(root, package, public, target='macos-arm64', check_codesign=False, check_arch=False)


def test_canonical_manifest_is_stable():
    payload = {'b': 1, 'a': [2, 3]}
    assert canonical(payload) == b'{"a":[2,3],"b":1}'


def test_zip_name_recovers_utf8_without_utf8_flag():
    from types import SimpleNamespace
    from qa_parser.macos_update import _zip_name
    raw = '题库转换.app/Contents/Info.plist'.encode('utf-8').decode('cp437')
    assert _zip_name(SimpleNamespace(filename=raw, flag_bits=0)) == '题库转换.app/Contents/Info.plist'
    assert _zip_name(SimpleNamespace(filename='plain.txt', flag_bits=0)) == 'plain.txt'


def test_update_bridge_writes_request_and_hands_off(tmp_path, monkeypatch):
    import qa_parser.desktop_window as desktop_window
    from qa_parser.desktop_window import UpdateBridge

    class FakeWindow:
        def __init__(self, path):
            self.path = path
            self.destroyed = False
        def create_file_dialog(self, *args, **kwargs):
            return (str(self.path),) if self.path else None
        def destroy(self):
            self.destroyed = True

    class FakeTimer:
        def __init__(self, delay, callback):
            self.callback = callback
        def start(self):
            self.callback()

    archive = tmp_path / 'update.qaupdate'
    archive.write_bytes(b'package')
    monkeypatch.setattr(desktop_window.threading, 'Timer', FakeTimer)
    launches = []
    monkeypatch.setattr(desktop_window.subprocess, 'Popen', lambda argv: launches.append(argv))

    root = tmp_path / 'installed'
    (root / '管理更新.app').mkdir(parents=True)
    bridge = UpdateBridge(root, 'macos', '1.0.0')
    bridge.window = FakeWindow(archive)
    assert bridge.update_info()['platform'] == 'macos'
    assert bridge.choose_update() == {'ok': True}
    request = json.loads((root / '.update-request.json').read_text())
    assert request['archive'] == str(archive)
    assert request['platform'] == 'macos'
    assert launches and '--install-request' in launches[0]
    assert bridge.window.destroyed

    bridge.window = FakeWindow(None)
    assert bridge.choose_update() == {'ok': False}
