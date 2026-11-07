#!/usr/bin/env python3
"""Inventory or reversibly archive an explicit set of superseded local artifacts.

Default: dry-run JSON only. --apply reads a reviewed manifest, rechecks snapshots
and local process/open-file inventory, and renames same-filesystem directories.
Never deletes files, never follows directory symlinks, never touches deliverables.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = {
    'outputs/phase1': 'Historical first-stage intermediate; docs/phase1-validation.md selects mac-acceptance as final record.',
    'outputs/phase1-v2': 'Historical first-stage intermediate; superseded by retained outputs/mac-acceptance.',
    'outputs/mac-phase1': 'Historical first-stage intermediate; superseded by retained outputs/mac-acceptance.',
    'outputs/mac-phase1-full': 'Historical first-stage intermediate; superseded by retained outputs/mac-acceptance.',
    'build/lib': 'Regenerable setuptools build copy; source remains in src/.',
    'build/bdist.macosx-15.0-arm64': 'Regenerable old wheel staging directory.',
}
PROTECTED = ['reference/', 'src/', 'tests/', 'assets/', 'config/', 'packaging/', '.venv/', '.git/',
             'dist/', 'build/windows/', 'build/desktop/', 'output/', 'outputs/display-*',
             'outputs/import-acceptance/', 'outputs/mac-acceptance/', 'outputs/windows-*',
             'outputs/bundled-all-nine/', 'outputs/分发 验证/', 'all unlisted paths']


def snapshot(folder: Path):
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError(f'Candidate must be an existing real directory: {folder}')
    entries = []
    total = 0
    count = 0
    for base, dirs, files in os.walk(folder, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(base) / name
            stat = path.lstat()
            relative = path.relative_to(folder).as_posix()
            target = os.readlink(path) if path.is_symlink() else None
            kind = 'symlink' if target is not None else 'directory' if path.is_dir() else 'file'
            entries.append((relative, kind, stat.st_size, stat.st_mtime_ns, target))
            if kind == 'file':
                total += stat.st_size
                count += 1
    digest = hashlib.sha256(json.dumps(sorted(entries), ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    return {'files': count, 'logical_bytes': total, 'metadata_sha256': digest}


def process_inventory():
    """Inspect only approved candidate trees; verify that empty lsof is reliable.

    lsof returns 1 when a target has no open files. This is accepted only after
    a positive control detects this process's deliberately opened script file.
    Any stderr, timeout or other exit code keeps the operation fail-closed.
    """
    if os.name != 'posix':
        return {'status': 'unavailable', 'reason': 'This local archive helper currently requires POSIX ps/lsof.'}
    stage = 'process table'
    try:
        ps = subprocess.run(['ps', '-axo', 'pid=,command='], capture_output=True, text=True, timeout=15, check=True)
        if ps.stderr.strip():
            raise RuntimeError('process table returned diagnostics')
        stage = 'lsof positive control'
        control = Path(__file__).resolve()
        with control.open('rb'):
            probe = subprocess.run(['lsof', '-nP', '-a', '-p', str(os.getpid()), '-Fpn', str(control)],
                                   capture_output=True, text=True, timeout=15, check=False)
        if probe.returncode != 0 or probe.stderr.strip() or 'n' + str(control) not in probe.stdout.splitlines():
            raise RuntimeError('lsof could not detect deliberately opened control file')
        paths = []
        checked = []
        for relative in CANDIDATES:
            folder = ROOT / relative
            if not folder.exists():
                continue
            stage = 'candidate open-file scan: ' + relative
            opened = subprocess.run(['lsof', '-nP', '-a', '-u', str(os.getuid()), '-Fpn', '+D', str(folder)],
                                    capture_output=True, text=True, timeout=20, check=False)
            if opened.returncode not in (0, 1) or opened.stderr.strip():
                raise RuntimeError(f'candidate lsof returned exit {opened.returncode} or diagnostics')
            matching = [line[1:] for line in opened.stdout.splitlines() if line.startswith('n')]
            if opened.returncode == 1 and matching:
                raise RuntimeError('inconsistent lsof exit/status')
            if any(path != str(folder) and not path.startswith(str(folder) + '/') for path in matching):
                raise RuntimeError('lsof returned a path outside requested candidate')
            paths.extend(matching)
            checked.append(relative)
        # Never put whole process commands or unrelated filenames in the manifest.
        return {'status': 'available', 'positive_control': 'passed', 'candidate_trees_checked': checked,
                '_commands': ps.stdout.splitlines(), '_paths': paths}
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        return {'status': 'unavailable', 'reason': type(error).__name__ + ': ' + stage + ' failed'}


def activity_hits(relative, activity):
    if activity['status'] != 'available':
        return []
    absolute = str(ROOT / relative)
    hits = []
    for line in activity['_commands']:
        if absolute in line or relative in line:
            pid = line.strip().split(None, 1)[0]
            if pid != str(os.getpid()):
                hits.append({'kind': 'process_argument', 'pid': pid})
    for path in activity['_paths']:
        if path == absolute or path.startswith(absolute + '/'):
            hits.append({'kind': 'open_file_or_cwd', 'path': path})
    return hits


def references(relative):
    hits = []
    for path in [ROOT / 'README.md', *(ROOT / 'docs').glob('*.md'), *(ROOT / 'scripts').glob('*.py')]:
        if path.name == Path(__file__).name or path.name == 'local-artifacts.md':
            continue
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            if relative in line:
                hits.append({'path': path.relative_to(ROOT).as_posix(), 'line': number})
    return hits


def create_plan():
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    activity = process_inventory()
    entries = []
    for relative, reason in CANDIDATES.items():
        source = ROOT / relative
        if not source.exists():
            continue
        entries.append({'source': relative, 'destination': f'outputs/archive/{stamp}/{relative}',
                        'reason': reason, 'snapshot': snapshot(source), 'active_hits': activity_hits(relative, activity),
                        'references': references(relative)})
    return {'schema_version': 1, 'mode': 'dry_run', 'project_root': str(ROOT), 'stamp': stamp,
            'created_at': datetime.now(timezone.utc).isoformat(), 'protected': PROTECTED,
            'activity_check': {k: v for k, v in activity.items() if not k.startswith('_')},
            'entries': entries, 'logical_bytes_to_relocate': sum(e['snapshot']['logical_bytes'] for e in entries),
            'disk_space_reclaimed': 0, 'note': 'Same-filesystem archive organizes files; it does not reclaim disk space or delete evidence.'}


def apply_plan(plan, manifest):
    if plan.get('schema_version') != 1 or plan.get('mode') != 'dry_run' or plan.get('project_root') != str(ROOT):
        raise ValueError('Not a dry-run manifest for this project')
    stamp = plan.get('stamp', '')
    if not stamp or any(c not in '0123456789TZ' for c in stamp):
        raise ValueError('Invalid archive stamp')
    activity = process_inventory()
    if activity['status'] != 'available':
        raise RuntimeError('Cannot establish active-process/open-file state; nothing moved')
    base = ROOT / 'outputs/archive'
    if base.is_symlink() or base.resolve() != base:
        raise ValueError('Archive root must not be a symlink')
    if (base / stamp).exists() or (base / stamp).is_symlink():
        raise FileExistsError('Archive run directory already exists; choose a fresh plan')
    checked = []
    seen = set()
    for entry in plan['entries']:
        relative = entry['source']
        if relative not in CANDIDATES or relative in seen:
            raise ValueError('Candidate outside closed allowlist or duplicate')
        seen.add(relative)
        source = ROOT / relative
        destination = base / stamp / relative
        if entry['destination'] != destination.relative_to(ROOT).as_posix() or destination.exists():
            raise ValueError('Destination differs or already exists')
        if snapshot(source) != entry['snapshot']:
            raise RuntimeError(f'Candidate changed since inventory: {relative}; nothing moved')
        if activity_hits(relative, activity):
            raise RuntimeError(f'Candidate is in use: {relative}; nothing moved')
        if source.stat().st_dev != ROOT.stat().st_dev:
            raise ValueError('Candidate is on another filesystem')
        checked.append((entry, source, destination))
    receipt = {'schema_version': 1, 'mode': 'archive_receipt', 'source_manifest': str(manifest),
               'project_root': str(ROOT), 'activity_check': {k: v for k, v in activity.items() if not k.startswith('_')},
               'moves': [], 'status': 'running'}
    receipt_path = manifest.with_name(manifest.stem + '-receipt.json')
    if receipt_path.exists():
        raise FileExistsError('Receipt already exists')
    try:
        for entry, source, destination in checked:
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.rename(destination)
            receipt['moves'].append({'source': entry['source'], 'destination': entry['destination'],
                                     'snapshot': entry['snapshot'], 'restore': f'Rename {entry["destination"]} back to {entry["source"]}; never overwrite existing content.'})
            receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
        receipt['status'] = 'completed'
    except Exception:
        receipt['status'] = 'partial; consult moves before retry or restore'
        raise
    finally:
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    return receipt_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=ROOT / 'outputs/local-archive-plan.json')
    parser.add_argument('--apply', action='store_true', help='Apply an already reviewed dry-run manifest; no deletions')
    args = parser.parse_args(argv)
    manifest = args.manifest.resolve()
    if args.apply:
        receipt = apply_plan(json.loads(manifest.read_text(encoding='utf-8')), manifest)
        print(f'Archived; receipt: {receipt}')
    else:
        if manifest.exists():
            raise FileExistsError('Choose a new manifest path; existing inventory is preserved')
        plan = create_plan()
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'Dry run only: {len(plan["entries"])} candidates, {plan["logical_bytes_to_relocate"]:,} logical bytes; {manifest}')
        print('No directories moved or deleted. Activity inspection: ' + plan['activity_check']['status'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
