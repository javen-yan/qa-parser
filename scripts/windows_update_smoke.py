#!/usr/bin/env python3
"""Native signed upgrade/damaged-start rollback acceptance on a disposable split installation."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from build_windows_distribution import signing_key, write_business_manifest, archive_tree
from qa_parser.windows_distribution import inventory, atomic_json
from qa_parser.windows_update import read_state


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if sys.platform != 'win32':
        p.error('Native Windows is required')
    a.out.mkdir(parents=True, exist_ok=False)
    root = a.out / '中文 更新安装'
    shutil.copytree(a.root, root)
    key = signing_key()
    initial = read_state(root)['current']
    before = inventory(root / 'runtime')
    source = root / 'app' / ('v' + initial)
    manifest = json.loads((source / 'app-manifest.json').read_text())
    # Synthetic versions are isolated in build/, never uploaded as release artifacts.
    newer = a.out / 'newer'
    shutil.copytree(source, newer)
    write_business_manifest(newer, '1.9998.0', manifest['runtime_id'], key)
    update = a.out / 'valid.qaupdate'
    archive_tree(newer, update)
    report = a.out / 'upgrade.json'
    subprocess.run([str(root / '题库转换.exe'), '--test-update', str(update), str(report)], check=True, timeout=180)
    if json.loads(report.read_text())['status'] != 'passed' or read_state(root)['current'] != '1.9998.0':
        raise ValueError('Native signed upgrade did not complete')
    (newer / '题库转换.exe').write_bytes(b'not a valid executable')
    write_business_manifest(newer, '1.9999.0', manifest['runtime_id'], key)
    bad = a.out / 'bad-start.qaupdate'
    archive_tree(newer, bad)
    failed = a.out / 'failed-start.json'
    result = subprocess.run([str(root / '题库转换.exe'), '--test-update', str(bad), str(failed)], timeout=180)
    if result.returncode == 0 or read_state(root)['current'] != '1.9998.0':
        raise ValueError('Native failed-start rollback did not restore the previous version')
    if inventory(root / 'runtime') != before:
        raise ValueError('Business update modified fixed runtime')
    atomic_json(a.out / 'update-validation.json', {'status': 'passed', 'native_windows': True,
        'signed_upgrade': True, 'startup_rollback': True, 'runtime_unchanged': True,
        'private_reference': 'not uploaded; not validated'})


if __name__ == '__main__':
    main()
