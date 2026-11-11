#!/usr/bin/env python3
"""Bind successful native execution and dependency closure to exact ZIP bytes."""
import argparse
import hashlib
import json
import os
from pathlib import Path


def write_metadata(archive, smoke, target, output, source_complete, startup=None, composite=None, windows_update=None):
    report = json.loads(smoke.read_text(encoding='utf-8'))
    if report.get('status') != 'passed' or report.get('gui', {}).get('status') != 'passed':
        raise ValueError('Native CLI and GUI smoke must pass before candidate metadata')
    with archive.open('rb') as stream:
        sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    data = {'schema_version': 1, 'status': 'passed', 'target': target, 'variant': 'light',
            'commit': os.environ.get('GITHUB_SHA', ''), 'zip_sha256': sha,
            'bytes': archive.stat().st_size, 'native_smoke_passed': True,
            'source_closure_complete': source_complete,
            'private_reference': 'not uploaded or validated', 'customer_environment': 'unverified',
            'run_url': f"https://github.com/{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}",
            'smoke': report}
    if startup is not None:
        diagnostic = json.loads(startup.read_text(encoding='utf-8'))
        if (diagnostic.get('status') != 'passed' or not diagnostic.get('outer_error_reproduced')
            or diagnostic.get('automatic_recovery', {}).get('status') != 'recovered_verified_internet_mark'):
            raise ValueError('Windows startup reproduction and automatic recovery must pass')
        data['startup_validation'] = diagnostic
    if composite is not None:
        business = json.loads(composite.read_text(encoding='utf-8'))
        runs = business.get('runs', {})
        if (business.get('status') != 'passed' or set(runs) != {'development', 'frozen'}
            or any(r.get('status') != 'passed' for r in runs.values())
            or not runs['development'].get('semantic_sha256')
            or runs['development']['semantic_sha256'] != runs['frozen'].get('semantic_sha256')):
            raise ValueError('Composite source and frozen semantics must both pass and match')
        data['composite_validation'] = business
    if windows_update is not None:
        if target != 'windows-x64':
            raise ValueError('Split metadata is Windows-only')
        from prepare_release import WINDOWS_EXTRAS, extra_artifacts
        validation = json.loads(windows_update.read_text())
        size_file = archive.parent / 'windows-distribution-size.json'
        sizes = json.loads(size_file.read_text())
        sizes.update(full_zip_bytes=archive.stat().st_size, native_validation=validation['status'])
        size_file.write_text(json.dumps(sizes, indent=2))
        data['windows_distribution'] = {'schema_version': 1, 'runtime_id': sizes['runtime_id'],
            'validation': validation, 'artifacts': []}
        for name in WINDOWS_EXTRAS:
            path = archive.parent / name
            with path.open('rb') as stream:
                checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
            data['windows_distribution']['artifacts'].append({'filename': name, 'bytes': path.stat().st_size, 'sha256': checksum})
        extra_artifacts(archive.parent, data)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--archive', type=Path, required=True)
    p.add_argument('--smoke', type=Path, required=True)
    p.add_argument('--target', choices=['windows-x64', 'macos-arm64', 'macos-x86_64'], required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--runtime-lock', type=Path, required=True)
    p.add_argument('--startup', type=Path)
    p.add_argument('--composite', type=Path)
    p.add_argument('--windows-update', type=Path)
    a = p.parse_args()
    lock = json.loads(a.runtime_lock.read_text(encoding='utf-8'))
    if a.target == 'windows-x64':
        complete = not lock['wmf']['missing_sources']
    else:
        complete = lock.get('source_closure_complete') is True
    write_metadata(a.archive, a.smoke, a.target, a.output, complete, a.startup, a.composite, a.windows_update)
