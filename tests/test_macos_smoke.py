"""Packaging guards are platform-neutral; real native runs remain CI evidence."""
import importlib.util
import json
import os
from pathlib import Path
import plistlib
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location('qa_macos_smoke_tests', Path(__file__).resolve().parents[1] / 'scripts/macos_smoke.py')
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def test_inventory_preserves_relative_symlinks_and_file_modes(tmp_path):
    app = tmp_path / '示例.app'
    resource = app / 'Contents/Resources/font'
    resource.parent.mkdir(parents=True)
    resource.write_bytes(b'font data')
    resource.chmod(0o644)
    link = app / 'Contents/font-link'
    # Windows test runners may lack symlink privilege: mocking the filesystem is
    # unnecessary here; test file metadata everywhere and symlink behavior only
    # where its creation is supported, without marking the whole test skipped.
    if os.name == 'posix':
        link.symlink_to('Resources/font')
    inventory = smoke.app_inventory(app)
    assert inventory['Contents/Resources/font']['sha256'] == smoke.shared.sha_file(resource)
    assert inventory['Contents/Resources/font']['kind'] == 'file'
    if os.name == 'posix':
        assert inventory['Contents/font-link'] == {'kind': 'symlink', 'target': 'Resources/font'}
        link.unlink()
        external = tmp_path / 'external'
        external.write_bytes(b'outside')
        link.symlink_to('../../external')
        with pytest.raises(AssertionError, match='escapes bundle'):
            smoke.app_inventory(app)


def test_rosetta_and_wrong_runner_architecture_cannot_claim_native(monkeypatch):
    monkeypatch.setattr(smoke.sys, 'platform', 'darwin')
    monkeypatch.setattr(smoke.platform, 'machine', lambda: 'x86_64')
    monkeypatch.setattr(smoke.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout='1\n'))
    with pytest.raises(AssertionError, match='Rosetta'):
        smoke.native_architecture('x86_64')
    with pytest.raises(AssertionError, match='differs from expected'):
        smoke.native_architecture('arm64')


def test_frozen_environment_removes_developer_loader_paths(monkeypatch):
    monkeypatch.setenv('PATH', '/developer/tools')
    monkeypatch.setenv('PYTHONPATH', '/developer/site-packages')
    monkeypatch.setenv('QA_PARSER_LIBREOFFICE_ROOT', '/developer/runtime')
    monkeypatch.setenv('DYLD_LIBRARY_PATH', '/developer/dylibs')
    env = smoke.frozen_environment()
    assert env['PATH'] == '/usr/bin:/bin:/usr/sbin:/sbin'
    assert not any(k in env for k in ('PYTHONPATH', 'QA_PARSER_LIBREOFFICE_ROOT', 'DYLD_LIBRARY_PATH'))


def test_resource_manifest_rejects_changed_font_before_native_tools(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    source_web = project / 'src/qa_parser/web'
    source_web.mkdir(parents=True)
    (source_web / 'math-preview.js').write_bytes(b'approved rendering code')
    monkeypatch.setattr(smoke, 'ROOT', project)
    app = tmp_path / '题库转换.app'
    contents = app / 'Contents'
    (contents / 'MacOS').mkdir(parents=True)
    executable = contents / 'MacOS/题库转换'
    executable.write_bytes(b'placeholder')
    executable.chmod(0o755)
    (contents / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable': '题库转换'}))
    bundled_web = contents / 'Resources/qa_parser/web'
    bundled_web.mkdir(parents=True)
    (bundled_web / 'math-preview.js').write_bytes(b'changed')
    with pytest.raises(AssertionError, match='display resource differs'):
        smoke.resource_evidence(app, 'arm64', {})


def test_lipo_architecture_is_checked_not_only_file_format(tmp_path, monkeypatch):
    def output(command, **kwargs):
        return 'Mach-O 64-bit executable x86_64' if command[0].endswith('/file') else 'x86_64'
    monkeypatch.setattr(smoke.subprocess, 'check_output', output)
    with pytest.raises(AssertionError, match='lacks arm64'):
        smoke.check_macho(tmp_path / 'binary', 'arm64')
    assert smoke.check_macho(tmp_path / 'binary', 'x86_64')['architectures'] == ['x86_64']
