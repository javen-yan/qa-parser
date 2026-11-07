#!/usr/bin/env python3
"""Bind successful native execution and dependency closure to exact ZIP bytes."""
import argparse
import hashlib
import json
import os
from pathlib import Path


def write_metadata(archive, smoke, target, output, source_complete):
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
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--archive', type=Path, required=True)
    p.add_argument('--smoke', type=Path, required=True)
    p.add_argument('--target', choices=['windows-x64', 'macos-arm64', 'macos-x86_64'], required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--runtime-lock', type=Path, required=True)
    a = p.parse_args()
    lock = json.loads(a.runtime_lock.read_text(encoding='utf-8'))
    if a.target == 'windows-x64':
        complete = not lock['wmf']['missing_sources']
    else:
        complete = lock.get('source_closure_complete') is True
    write_metadata(a.archive, a.smoke, a.target, a.output, complete)
