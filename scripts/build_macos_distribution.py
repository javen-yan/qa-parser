#!/usr/bin/env python3
"""Build the macOS folder distribution and its independent update manager.

The candidate ZIP root contains the business app, the manager, the initial
install state and a short instruction file.  The same ZIP is the offline update
source for later releases; the manager verifies a signed sidecar manifest against
the new app before replacing anything.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from qa_parser.macos_distribution import (APP_NAME, MANAGER_NAME, TARGETS, UPDATE_MANIFEST,
                                         atomic_json, verify_app, write_update_manifest)

PUBLIC_KEY = ROOT / 'packaging/update-public-key.txt'


def pinned_key():
    return PUBLIC_KEY.read_text(encoding='utf-8').strip().lower()


def verify_key_material():
    pinned = pinned_key()
    expected = os.environ.get('QA_UPDATE_PUBLIC_KEY_HEX', '').strip().lower()
    if expected and expected != pinned:
        raise ValueError('QA_UPDATE_PUBLIC_KEY_HEX does not match the pinned public key')
    raw = os.environ.get('QA_UPDATE_PRIVATE_KEY_HEX', '').strip()
    if raw:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        try:
            key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(raw))
        except ValueError as error:
            raise ValueError('QA_UPDATE_PRIVATE_KEY_HEX must be a 32-byte Ed25519 seed') from error
        if key.public_key().public_bytes_raw().hex() != pinned:
            raise ValueError('QA_UPDATE_PRIVATE_KEY_HEX does not match the pinned public key')
    return bytes.fromhex(pinned)


def build_manager(destination, architecture, public_key, python_licenses):
    work = ROOT / 'build/macos-manager'
    work.mkdir(parents=True, exist_ok=True)
    (work / 'qa_update_key.py').write_text('PUBLIC_KEY_HEX = ' + repr(public_key.hex()) + '\n')
    entry = work / 'entry.py'
    entry.write_text('from qa_parser.macos_manager import main\n'
                     'if __name__ == "__main__":\n    raise SystemExit(main())\n')
    command = [sys.executable, '-m', 'PyInstaller', '--clean', '--noconfirm', '--windowed', '--onedir',
               '--name', '管理更新', '--osx-bundle-identifier', 'local.qa-parser.updater',
               '--target-arch', architecture, '--contents-directory', '管理入口依赖',
               '--paths', str(ROOT / 'src'), '--paths', str(work), '--hidden-import', 'qa_update_key',
               '--distpath', str(work / 'dist'), '--workpath', str(work / 'freeze'), '--specpath', str(work),
               '--exclude-module', 'webview', '--exclude-module', 'tkinter', '--exclude-module', 'pytest',
               '--icon', str(ROOT / 'assets/icon.icns'), str(entry)]
    subprocess.run(command, check=True)
    source = work / 'dist' / MANAGER_NAME
    if not source.is_dir():
        raise RuntimeError('PyInstaller did not produce 管理更新.app')
    shutil.copytree(source, destination, symlinks=True)
    if python_licenses.is_dir():
        shutil.copytree(python_licenses, destination / 'Contents/Resources/licenses', dirs_exist_ok=True)
    subprocess.run(['/usr/bin/codesign', '--force', '--deep', '--sign', '-', str(destination)], check=True)
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(destination)], check=True)


def tree_bytes(root):
    return sum(p.stat().st_size for p in Path(root).rglob('*') if p.is_file() and not p.is_symlink())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, default=ROOT / 'dist/macos/题库转换.app')
    parser.add_argument('--out', type=Path, default=ROOT / 'dist/macos-split/题库转换')
    parser.add_argument('--architecture', choices=tuple(TARGETS), default=platform.machine())
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--size', type=Path, default=ROOT / 'dist/macos-distribution-size.json')
    args = parser.parse_args()
    app = args.app.resolve()
    out = args.out.resolve()
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
    target = TARGETS[args.architecture]
    public = verify_key_material()
    if args.architecture != platform.machine():
        raise ValueError('Native macOS build required for the requested architecture')
    if out.exists():
        raise RuntimeError('Use a fresh distribution output directory')
    out.mkdir(parents=True)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    raw = os.environ.get('QA_UPDATE_PRIVATE_KEY_HEX', '').strip()
    if not raw:
        raise RuntimeError('QA_UPDATE_PRIVATE_KEY_HEX is required to sign the macOS update manifest')
    data = write_update_manifest(app, target, version, Ed25519PrivateKey.from_private_bytes(bytes.fromhex(raw)), out)
    if data['version'] != version:
        raise ValueError('Application manifest version differs from pyproject.toml')
    shutil.copytree(app, out / APP_NAME, symlinks=True)
    verify_app(out / APP_NAME, out / UPDATE_MANIFEST, public, target=target)
    build_manager(out / MANAGER_NAME, args.architecture, public,
                  out / APP_NAME / 'Contents/Resources/licenses/python')
    atomic_json(out / 'install-state.json', {
        'schema_version': 1, 'current': version, 'previous': None, 'pending': None})
    (out / '使用说明.txt').write_text(
        '完整解压后双击“题库转换.app”使用。\n'
        '离线更新：关闭所有题库转换窗口并等待转换结束，双击“管理更新.app”，'
        '选择新的完整分发 ZIP；也可恢复上一版本。\n'
        '更新包需要匹配 Mac 架构；不联网，不修改源 Word 和已生成结果。\n'
        '请保留 app、管理器和 install-state.json，不要只复制单个 app。\n', encoding='utf-8')
    candidate = (args.candidate or ROOT / f'dist/qa-parser-macos-{args.architecture}-candidate.zip').resolve()
    if candidate.exists():
        raise RuntimeError('Candidate ZIP already exists')
    subprocess.run(['/usr/bin/ditto', '-c', '-k', '--norsrc', '.', str(candidate)],
                   cwd=out, check=True)
    sizes = {'schema_version': 1, 'version': version, 'target': target,
             'application_bytes': tree_bytes(out / APP_NAME),
             'manager_bytes': tree_bytes(out / MANAGER_NAME),
             'full_zip_bytes': candidate.stat().st_size,
             'full_uncompressed_bytes': tree_bytes(out),
             'update_manifest': {'version': data['version'], 'target': data['target'],
                                 'files': len(data['files'])},
             'native_validation': 'pending'}
    atomic_json(args.size.resolve(), sizes)
    print(json.dumps(sizes, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
