#!/usr/bin/env python3
"""Native macOS distribution and signed update/rollback acceptance.

Uses the actual candidate ZIP.  A synthetic next version is created from the
same app by bumping Info.plist and signing a new manifest, then the independent
manager performs a real upgrade and rollback through the GUI smoke path.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from qa_parser.macos_distribution import (APP_NAME, MANAGER_NAME, TARGETS, UPDATE_MANIFEST,
                                         digest, read_state, verify_app, write_update_manifest)

PUBLIC_KEY = ROOT / 'packaging/update-public-key.txt'


def run(command, **kwargs):
    subprocess.run([str(v) for v in command], check=True, **kwargs)


def private_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    raw = os.environ.get('QA_UPDATE_PRIVATE_KEY_HEX', '').strip()
    if not raw:
        raise RuntimeError('QA_UPDATE_PRIVATE_KEY_HEX is required for the macOS update smoke')
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(raw))
    pinned = PUBLIC_KEY.read_text(encoding='utf-8').strip().lower()
    if key.public_key().public_bytes_raw().hex() != pinned:
        raise RuntimeError('QA_UPDATE_PRIVATE_KEY_HEX does not match the pinned public key')
    return key


def next_version(version):
    major, minor, patch = map(int, version.split('.'))
    return f'{major}.{minor}.{patch + 1}'


def prepare_update(source_app, destination, target, version, key):
    shutil.copytree(source_app, destination / APP_NAME, symlinks=True)
    app = destination / APP_NAME
    plist_path = app / 'Contents/Info.plist'
    with plist_path.open('rb') as stream:
        plist = plistlib.load(stream)
    plist['CFBundleShortVersionString'] = version
    plist['CFBundleVersion'] = version
    with plist_path.open('wb') as stream:
        plistlib.dump(plist, stream)
    run(['/usr/bin/codesign', '--force', '--deep', '--sign', '-', app])
    run(['/usr/bin/codesign', '--verify', '--deep', '--strict', app])
    write_update_manifest(app, target, version, key, destination)
    verify_app(app, destination / UPDATE_MANIFEST, key.public_key().public_bytes_raw(), target=target)
    archive = destination.parent / 'synthetic-update.zip'
    run(['/usr/bin/ditto', '-c', '-k', '--norsrc', '.', archive], cwd=destination)
    return archive


def manager_run(root, args, **kwargs):
    executable = Path(root) / MANAGER_NAME / 'Contents/MacOS/管理更新'
    return subprocess.run([str(executable), '--root', str(root), *map(str, args)],
                          check=False, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--architecture', choices=tuple(TARGETS), default=platform.machine())
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists():
        raise RuntimeError('Use a fresh update smoke output directory')
    out.mkdir(parents=True)
    root = out / 'installed'
    run(['/usr/bin/ditto', '-x', '-k', args.candidate, root])
    app = root / APP_NAME
    manager = root / MANAGER_NAME
    if not app.is_dir() or not manager.is_dir():
        raise RuntimeError('Candidate ZIP is not a complete macOS distribution')
    key = private_key()
    public = bytes.fromhex(PUBLIC_KEY.read_text(encoding='utf-8').strip())
    target = TARGETS[args.architecture]
    state = read_state(root)
    original = state['current']
    updated = next_version(original)
    verify_app(app, root / UPDATE_MANIFEST, public, target=target)
    run(['/usr/bin/codesign', '--verify', '--deep', '--strict', manager])
    update = prepare_update(app, out / 'next', target, updated, key)
    report = out / 'update-smoke.json'
    result = manager_run(root, ['--test-update', update, report], timeout=240)
    manager_report = json.loads(report.with_suffix('.manager.json').read_text(encoding='utf-8'))
    if result.returncode != 0 or manager_report.get('status') != 'passed':
        raise RuntimeError(f'Manager upgrade failed: {manager_report}')
    after = read_state(root)
    if (after['current'], after['previous'], after['pending']) != (updated, original, None):
        raise RuntimeError(f'Unexpected state after upgrade: {after}')
    rollback_report = out / 'rollback-smoke.json'
    result = manager_run(root, ['--rollback', '--test-report', rollback_report], timeout=240)
    restored = read_state(root)
    if result.returncode != 0 or restored['current'] != original or restored['pending'] is not None:
        raise RuntimeError(f'Manager rollback failed: {restored}')
    verify_app(app, root / UPDATE_MANIFEST, public, target=target)
    evidence = {'schema_version': 1, 'status': 'passed', 'platform': platform.platform(),
                'architecture': args.architecture, 'target': target,
                'native_macos': True, 'signed_upgrade': True, 'rollback': True,
                'candidate': str(args.candidate.resolve()),
                'candidate_sha256': digest(args.candidate), 'candidate_bytes': args.candidate.stat().st_size,
                'original_version': original, 'updated_version': updated,
                'manager': manager_report, 'state_after_rollback': restored}
    (out / 'update-validation.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(evidence, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
