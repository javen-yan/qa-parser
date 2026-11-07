#!/usr/bin/env python3
"""Native macOS candidate acceptance using public synthetic inputs only.

Builds the actual distributable ZIP with ditto, extracts it preserving symlinks,
then runs the relocated frozen application with only system tools on PATH.
A passed --gui run proves the runner's Cocoa/WebKit session, not customer Macs.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import plistlib
import stat
import subprocess
import sys
from zipfile import ZipFile

# Reuse identical synthetic content/semantic assertions across Windows and Mac.
_spec = importlib.util.spec_from_file_location('qa_public_smoke_helpers', Path(__file__).with_name('windows_smoke.py'))
shared = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shared)
ROOT = Path(__file__).resolve().parents[1]
require = shared.require
MACHO_MAGIC = {b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf',
               b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca',
               b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'}


def app_inventory(app: Path):
    """Hash physical files and preserve symlink targets without following links."""
    app = app.absolute()
    require(app.is_dir() and not app.is_symlink(), 'Application must be a real directory')
    inventory = {}
    for base, dirs, files in os.walk(app, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(base) / name
            relative = path.relative_to(app).as_posix()
            metadata = path.lstat()
            if path.is_symlink():
                target = os.readlink(path)
                require(not Path(target).is_absolute(), f'Absolute application symlink: {relative}')
                resolved = path.resolve(strict=True)
                require(resolved.is_relative_to(app.resolve()), f'Application symlink escapes bundle: {relative}')
                inventory[relative] = {'kind': 'symlink', 'target': target}
            elif path.is_file():
                inventory[relative] = {'kind': 'file', 'bytes': metadata.st_size,
                                       'sha256': shared.sha_file(path), 'mode': stat.S_IMODE(metadata.st_mode)}
            elif path.is_dir():
                inventory[relative] = {'kind': 'directory', 'mode': stat.S_IMODE(metadata.st_mode)}
            else:
                raise ValueError(f'Unsupported application entry: {relative}')
    return inventory


def native_architecture(expected=None):
    require(sys.platform == 'darwin', 'Native macOS is required')
    machine = platform.machine()
    require(machine in ('arm64', 'x86_64'), f'Unsupported native machine: {machine}')
    if expected:
        require(machine == expected, f'Runner architecture {machine} differs from expected {expected}')
    translated = subprocess.run(['/usr/sbin/sysctl', '-in', 'sysctl.proc_translated'], capture_output=True, text=True, check=False, timeout=10)
    require(not (translated.returncode == 0 and translated.stdout.strip() == '1'), 'Rosetta execution cannot count as native architecture acceptance')
    return machine


def check_macho(path: Path, expected: str):
    description = subprocess.check_output(['/usr/bin/file', '-b', str(path)], text=True, timeout=10).strip()
    require('Mach-O' in description, f'Expected Mach-O binary: {path}')
    arches = subprocess.check_output(['/usr/bin/lipo', '-archs', str(path)], text=True, timeout=10).split()
    require(expected in arches, f'Native binary lacks {expected}: {path} ({arches})')
    return {'architectures': arches, 'file': description}


def resource_evidence(app: Path, expected: str, inventory):
    contents = app / 'Contents'
    with (contents / 'Info.plist').open('rb') as stream:
        plist = plistlib.load(stream)
    name = plist.get('CFBundleExecutable')
    require(isinstance(name, str) and name == '题库转换', 'Unexpected application executable identity')
    executable = contents / 'MacOS' / name
    require(executable.is_file() and os.access(executable, os.X_OK), 'Application executable missing or not executable')
    required_web = ROOT / 'src/qa_parser/web'
    web = contents / 'Resources/qa_parser/web'
    require((required_web / 'math-preview.js').is_file(), 'Source display resources unavailable for comparison')
    checked_web = 0
    for source in required_web.rglob('*'):
        if source.is_file() and '__pycache__' not in source.parts:
            target = web / source.relative_to(required_web)
            require(target.is_file() and shared.sha_file(source) == shared.sha_file(target), f'Frozen display resource differs: {target}')
            checked_web += 1
    frameworks = contents / 'Frameworks'
    wmf = frameworks / 'wmf/bin/wmf2gd'
    require(wmf.is_file() and os.access(wmf, os.X_OK), 'Bundled WMF executable missing')
    require((frameworks / 'wmf/fonts/fontmap').is_file(), 'Bundled WMF fontmap missing')
    manifest_file = frameworks / 'resources-manifest.json'
    manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
    require(manifest.get('schema_version') == 1 and manifest.get('platform') == 'darwin' and manifest.get('architecture') == expected, 'Unexpected native resource manifest platform/architecture')
    listed = set()
    for entry in manifest['files']:
        relative = entry['path']
        require(isinstance(relative, str) and not relative.startswith('/') and '\\' not in relative and ':' not in relative and '..' not in relative.split('/'), 'Unsafe manifest path')
        require(relative not in listed, 'Duplicate resource manifest entry')
        listed.add(relative)
        target = frameworks / relative
        require(target.is_file() and target.stat().st_size == entry['bytes'] and shared.sha_file(target) == entry['sha256'], f'Bundled native resource missing or changed: {relative}')
    actual = {path.relative_to(frameworks).as_posix() for folder in ('wmf', 'licenses')
              for path in (frameworks / folder).rglob('*') if path.is_file()}
    require(listed == actual, 'Native resource manifest does not cover staged runtimes exactly')
    binaries = {}
    for relative, metadata in inventory.items():
        if metadata['kind'] != 'file':
            continue
        path = app / relative
        with path.open('rb') as stream:
            magic = stream.read(4)
        if magic in MACHO_MAGIC:
            binaries[relative] = check_macho(path, expected)
    require('Contents/MacOS/' + name in binaries and 'Contents/Frameworks/wmf/bin/wmf2gd' in binaries, 'Required Mach-O binaries were not verified')
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)], capture_output=True, text=True, timeout=30, check=True)
    return executable, {'display_files_compared_to_source': checked_web, 'native_manifest_files': len(listed),
        'macho_binaries': binaries, 'signature_integrity': 'verified; not a notarization or customer Gatekeeper claim'}


def relocate_app(app: Path, output: Path, expected: str):
    app = app.resolve()
    require(app.suffix == '.app', 'Expected a .app bundle')
    before = app_inventory(app)
    _, evidence = resource_evidence(app, expected, before)
    archive = output / 'portable-candidate.zip'
    require(not archive.exists(), 'Candidate archive already exists')
    subprocess.run(['/usr/bin/ditto', '-c', '-k', '--norsrc', '--keepParent', str(app), str(archive)], check=True, timeout=180)
    with ZipFile(archive) as z:
        require(z.testzip() is None, 'Application ZIP CRC failed')
        names = z.namelist()
        require(not any('__MACOSX' in Path(name).parts or any(part.startswith('._') for part in Path(name).parts) for name in names), 'Resource-fork metadata leaked into application ZIP')
        require(all(not name.startswith('/') and '..' not in name.split('/') for name in names), 'Unsafe application ZIP path')
    destination = output / '中文 解压'
    destination.mkdir()
    subprocess.run(['/usr/bin/ditto', '-x', '-k', str(archive), str(destination)], check=True, timeout=180)
    relocated = destination / app.name
    require(app_inventory(relocated) == before, 'Unpacked application bytes, symlinks or modes differ')
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(relocated)], capture_output=True, text=True, timeout=30, check=True)
    evidence.update(archive=str(archive), zip_sha256=shared.sha_file(archive),
                    entries=len(before), symlinks=sum(v['kind'] == 'symlink' for v in before.values()),
                    relocation='ditto ZIP extracted to Chinese and space path; bytes, modes and symlinks preserved')
    return relocated, before, evidence


def frozen_environment():
    env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PATH='/usr/bin:/bin:/usr/sbin:/sbin')
    for key in list(env):
        if key in ('PYTHONPATH', 'PYTHONHOME') or key.startswith(('QA_PARSER_', 'DYLD_')):
            env.pop(key)
    return env


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--expected-arch', choices=('arm64', 'x86_64'))
    parser.add_argument('--gui', action='store_true')
    args = parser.parse_args(argv)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {'schema_version': 1, 'status': 'failed', 'platform': platform.platform(),
              'scope': 'public synthetic inputs; native macOS candidate',
              'private_reference': 'not accessed; nine-document/900-question acceptance NOT performed',
              'customer_environment': 'unverified', 'notarization': 'unverified',
              'gui': 'not requested; no native window acceptance', 'runs': {}}
    try:
        architecture = native_architecture(args.expected_arch)
        report['native_architecture'] = architecture
        app, before, report['resources'] = relocate_app(args.app, out, architecture)
        executable = app / 'Contents/MacOS/题库转换'
        inputs, png = shared.create_inputs(out / '合成 输入')
        baselines = {}
        frozen_env = frozen_environment()
        for name, command in [('development', [sys.executable, '-m', 'qa_parser']), ('frozen', [str(executable), '--cli'])]:
            base = out / name
            base.mkdir()
            env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONPATH=str(ROOT / 'src')) if name == 'development' else frozen_env
            for kind in ('choice', 'blank'):
                folder = base / ('中文 ' + kind)
                shared.execute(command, ['run', inputs[kind], '--kind', kind, '--out', folder], base, env, base / (kind + '.log'))
                semantic = shared.check_run(folder, kind, png)
                if name == 'development':
                    baselines[kind] = semantic
                else:
                    require(semantic == baselines[kind], f'Development/frozen {kind} semantic mismatch')
            broken = base / '损坏结果'
            shared.execute(command, ['run', inputs['broken'], '--kind', 'choice', '--out', broken], base, env, base / 'broken.log', expected=1)
            require(not (broken / 'export/questions.xlsx').exists(), 'Corrupt input produced false successful workbook')
            batch = base / '批量 结果'
            shared.execute(command, ['batch', inputs['choice'], inputs['blank'], '--out', batch], base, env, base / 'batch.log')
            report['runs'][name] = shared.check_batch(batch)
        wmf_input = out / 'synthetic-original.wmf'
        wmf_input.write_bytes(shared.synthetic_wmf())
        wmf_report = out / 'native-wmf.json'
        shared.execute([str(executable)], ['--runtime-smoke', wmf_input, wmf_report], out, frozen_env, out / 'native-wmf.log')
        native = json.loads(wmf_report.read_text(encoding='utf-8'))
        require(native['status'] == 'converted' and native['width'] > 0 and native['height'] > 0, 'Bundled WMF conversion failed')
        report['wmf_runtime'] = native
        if args.gui:
            gui_report = out / 'native-window.json'
            shared.execute([str(executable)], ['--desktop-smoke', gui_report], out, frozen_env, out / 'native-window.log', timeout=90)
            gui = json.loads(gui_report.read_text(encoding='utf-8'))
            require(gui['status'] == 'passed', 'Native Cocoa/WebKit smoke failed')
            report['gui'] = gui
        require(app_inventory(app) == before, 'Application wrote or changed content inside its own bundle')
        report.update(status='passed', frozen_matches_development=True, restricted_frozen_path=True,
                      program_directory_content_unchanged=True)
        return 0
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        (out / 'smoke-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    raise SystemExit(main())
