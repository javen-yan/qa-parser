#!/usr/bin/env python3
"""Build immutable Windows runtime + signed business package from a native onedir build."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from qa_parser.windows_distribution import canonical, digest, inventory, atomic_json, PROTOCOL, TARGET


def signing_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    # Secrets are consumed in memory; never included in command lines or generated sources.
    raw = os.environ.get('QA_UPDATE_PRIVATE_KEY_HEX', '')
    expected = os.environ.get('QA_UPDATE_PUBLIC_KEY_HEX', '')
    try:
        key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(raw))
        public = key.public_key().public_bytes_raw().hex()
    except ValueError:
        raise ValueError('Configure QA_UPDATE_PRIVATE_KEY_HEX (32-byte Ed25519 seed)') from None
    pinned = (ROOT / 'packaging/windows-update-public-key.txt').read_text().strip()
    if public != expected.lower() or public != pinned:
        raise ValueError('Signing key does not match configured QA_UPDATE_PUBLIC_KEY_HEX')
    return key


def write_business_manifest(app, version, runtime_id, key):
    manifest = {'schema_version': 1, 'target': TARGET, 'version': version,
                'launcher_protocol': PROTOCOL, 'runtime_id': runtime_id,
                'files': inventory(app, ('app-manifest.json', 'app-manifest.sig'))}
    raw = canonical(manifest)
    (app / 'app-manifest.json').write_bytes(raw)
    (app / 'app-manifest.sig').write_bytes(key.sign(raw))
    return manifest


def archive_tree(root, archive, prefix=''):
    with ZipFile(archive, 'x', ZIP_DEFLATED, compresslevel=6) as z:
        for path in sorted(root.rglob('*')):
            if path.is_file():
                entry = ZipInfo(prefix + path.relative_to(root).as_posix(), (2020, 1, 1, 0, 0, 0))
                entry.compress_type = ZIP_DEFLATED
                entry.external_attr = 0o100644 << 16
                with path.open('rb') as source, z.open(entry, 'w') as target:
                    shutil.copyfileobj(source, target, 1024 * 1024)


def split(source, destination, version, key):
    destination.mkdir(parents=True, exist_ok=False)
    business = destination / 'app' / ('v' + version)
    shutil.copytree(source, business)
    native = destination / 'runtime' / 'staging'
    native.mkdir(parents=True)
    for name in ('webview2', 'wmf'):
        shutil.move(str(business / '_internal' / name), native / name)
    # Python licenses follow the business interpreter; native provenance follows the runtime.
    native_licenses = native / 'licenses'
    shutil.move(str(business / '_internal/licenses'), native_licenses)
    (business / '_internal/licenses').mkdir()
    shutil.move(str(native_licenses / 'python'), business / '_internal/licenses/python')
    shutil.move(str(business / '_internal/resources-manifest.json'), native / 'resources-manifest.json')
    provenance = native / 'resources-manifest.json'
    data = json.loads(provenance.read_text())
    data['files'] = [entry for entry in data['files'] if not entry['path'].startswith('licenses/python/')]
    atomic_json(provenance, data)
    runtime_manifest = {'schema_version': 1, 'target': TARGET, 'files': inventory(native)}
    runtime_id = 'r1-' + hashlib.sha256(canonical(runtime_manifest)).hexdigest()
    atomic_json(native / 'runtime-manifest.json', runtime_manifest)
    native.rename(native.with_name(runtime_id))
    manifest = write_business_manifest(business, version, runtime_id, key)
    atomic_json(destination / 'install-state.json', {'schema_version': 1, 'current': version, 'previous': None, 'pending': None})
    return manifest


def build_manager(destination, key):
    work = ROOT / 'build/windows-manager'
    work.mkdir(parents=True, exist_ok=True)
    (work / 'qa_update_key.py').write_text('PUBLIC_KEY_HEX = ' + repr(key.public_key().public_bytes_raw().hex()) + '\n')
    entry = work / 'entry.py'
    entry.write_text('from qa_parser.windows_manager import main\nif __name__ == "__main__":\n    raise SystemExit(main())\n')
    # No browser/GUI toolkit in the manager. Native Win32 dialogs only.
    command = [sys.executable, '-m', 'PyInstaller', '--clean', '--noconfirm', '--onedir', '--windowed',
               '--name', '题库转换', '--contents-directory', '管理入口依赖',
               '--paths', str(ROOT / 'src'), '--paths', str(work), '--hidden-import', 'qa_update_key',
               '--distpath', str(work / 'dist'), '--workpath', str(work / 'freeze'), '--specpath', str(work),
               '--exclude-module', 'webview', '--exclude-module', 'tkinter', '--exclude-module', 'pytest',
               '--icon', str(ROOT / 'assets/icon.ico'), str(entry)]
    subprocess.run(command, check=True)
    shutil.copytree(work / 'dist/题库转换', destination, dirs_exist_ok=True)
    # Signing/crypto notices also accompany the independent manager.
    shutil.copytree(destination / 'app' / next((destination / 'app').iterdir()).name / '_internal/licenses/python',
                    destination / '管理入口依赖/licenses')
    (destination / '管理更新.cmd').write_bytes(b'@echo off\r\ncd /d "%~dp0"\r\n' +
        '"题库转换.exe" --manage\r\n'.encode('gbk'))
    (destination / '使用说明.txt').write_text('完整解压后双击题库转换.exe。\n离线更新：关闭程序，打开“管理更新.cmd”，选择 .qaupdate 文件。\n'
        '运行时不兼容时请安装新完整包。请保留所有子目录、许可证和源码归档。\n', encoding='utf-8')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--source', type=Path, default=ROOT / 'dist/windows/题库转换')
    p.add_argument('--out', type=Path, default=ROOT / 'dist/windows-split/题库转换')
    a = p.parse_args()
    if sys.platform != 'win32':
        p.error('Native Windows build required')
    key = signing_key()
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
    manifest = split(a.source, a.out, version, key)
    build_manager(a.out, key)
    update = ROOT / 'dist/qa-parser-windows-x64.qaupdate'
    archive_tree(a.out / 'app' / ('v' + version), update)
    runtime = ROOT / 'dist/qa-parser-windows-x64-runtime.zip'
    archive_tree(a.out / 'runtime' / manifest['runtime_id'], runtime, manifest['runtime_id'] + '/')
    sizes = {'schema_version': 1, 'version': version, 'runtime_id': manifest['runtime_id'],
             'business_zip_bytes': update.stat().st_size, 'runtime_zip_bytes': runtime.stat().st_size,
             'business_uncompressed_bytes': sum(v['bytes'] for v in manifest['files'].values()),
             'full_uncompressed_bytes': sum(v['bytes'] for v in inventory(a.out).values()),
             'native_validation': 'pending'}
    atomic_json(ROOT / 'dist/windows-distribution-size.json', sizes)
    print(json.dumps(sizes))


if __name__ == '__main__':
    main()
