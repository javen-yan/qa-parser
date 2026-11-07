#!/usr/bin/env python3
"""Build a native Mac .app using the current, locked development environment.

No downloads occur here. Homebrew is a build-time input only. The renderer,
its recursive non-system dylibs and fonts are copied into the application.
"""
from __future__ import annotations
import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / 'packaging' / 'vendor-sources'
APP_NAME = '题库转换'


def run(*args):
    subprocess.run([str(a) for a in args], check=True)


def dependencies(binary: Path):
    text = subprocess.check_output(['otool', '-L', str(binary)], text=True)
    return [line.strip().split(' (compatibility')[0] for line in text.splitlines()[1:]]


def stage_wmf(destination: Path) -> list[dict]:
    executable = shutil.which('wmf2gd')
    if not executable:
        raise SystemExit('Build requires libwmf/wmf2gd; it is never downloaded at runtime.')
    source = Path(executable).resolve()
    cellar = source.parent.parent
    target = destination / 'bin'
    target.mkdir(parents=True)
    inventory = []
    pending = [source]
    copied = {}
    while pending:
        item = pending.pop().resolve()
        if item in copied:
            continue
        output = target / item.name
        if output.exists():
            raise RuntimeError(f'Native library filename collision: {item.name}')
        shutil.copy2(item, output)
        output.chmod(0o755)
        copied[item] = output
        deps = dependencies(item)
        for dep in deps:
            if dep.startswith(('/usr/lib/', '/System/Library/')):
                continue
            if not dep.startswith('/'):
                raise RuntimeError(f'Unexpected non-absolute original dependency: {dep}')
            resolved = Path(dep).resolve()
            if resolved != item:
                pending.append(resolved)
            run('install_name_tool', '-change', dep, '@loader_path/' + resolved.name, output)
        if item.suffix == '.dylib':
            run('install_name_tool', '-id', '@loader_path/' + item.name, output)
        inventory.append({'name': item.name, 'source': str(item),
                          'original_sha256': hashlib.sha256(item.read_bytes()).hexdigest()})
    for output in copied.values():
        run('codesign', '--force', '--sign', '-', output)
        for dep in dependencies(output):
            if not dep.startswith(('/usr/lib/', '/System/Library/', '@loader_path/')):
                raise RuntimeError(f'Unrelocated dependency: {dep}')
    shutil.copytree(cellar / 'share/libwmf/fonts', destination / 'fonts')
    # The runtime replaces these basenames with the actual relocated font path.
    fontmap = destination / 'fonts/fontmap'
    fontmap.write_text(re.sub(r'="[^"\n]*/([^"/]+\.(?:afm|t1))"', r'="\1"',
                             fontmap.read_text()), encoding='utf-8')
    return inventory


def stage_licenses(destination: Path):
    destination.mkdir(parents=True)
    inventory = []
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata['Name'].lower()):
        name = dist.metadata['Name']
        if name.lower() in {'qa-parser', 'pytest', 'iniconfig', 'pluggy', 'pygments', 'build', 'pyproject_hooks', 'pip'}:
            continue
        folder = destination / name
        files = []
        for entry in dist.files or []:
            if any(re.match(r'^(license|copying|notice|authors)', part, re.I) for part in Path(entry).parts):
                original = Path(dist.locate_file(entry))
                if original.is_file():
                    folder.mkdir(exist_ok=True)
                    output = folder / (str(entry).replace('/', '__'))
                    shutil.copy2(original, output)
                    files.append(output.name)
        if name == 'proxy_tools':
            folder.mkdir(exist_ok=True)
            shutil.copy2(SOURCES / 'proxy_tools-LICENSE.txt', folder / 'UPSTREAM-LICENSE.txt')
            files.append('UPSTREAM-LICENSE.txt (BSD; wheel metadata says MIT)')
        if name.lower().startswith('pyobjc') and not files:
            folder.mkdir(exist_ok=True)
            cocoa = metadata.distribution('pyobjc-framework-Cocoa')
            for entry in cocoa.files or []:
                if str(entry).endswith('licenses/LICENSE.txt'):
                    shutil.copy2(cocoa.locate_file(entry), folder / 'PyObjC-MIT-LICENSE.txt')
                    files.append('PyObjC-MIT-LICENSE.txt (same-version Cocoa distribution)')
        inventory.append({'name': name, 'version': dist.version,
                          'license': dist.metadata.get('License-Expression') or dist.metadata.get('License', ''),
                          'license_files': files})
    for package in ('libwmf', 'freetype', 'jpeg-turbo', 'libpng'):
        prefix = Path(subprocess.check_output(['brew', '--prefix', package], text=True).strip()).resolve()
        folder = destination / package
        folder.mkdir(exist_ok=True)
        for file in prefix.iterdir():
            if file.is_file() and (re.match(r'^(license|copying|notice|authors|readme)', file.name, re.I)
                                   or file.name in ('INSTALL_RECEIPT.json', 'sbom.spdx.json')):
                shutil.copy2(file, folder / file.name)
        formula = prefix / '.brew' / (package + '.rb')
        if formula.exists():
            shutil.copy2(formula, folder / formula.name)
        for file in (prefix / 'share/doc').rglob('*') if (prefix / 'share/doc').exists() else []:
            if file.is_file() and re.match(r'^(license|copying|notice|readme)', file.name, re.I):
                shutil.copy2(file, folder / file.name)
    archive = SOURCES / 'libwmf-0.2.13.tar.gz'
    expected = '18ba69febd2f515d98a2352de284a8051896062ac9728d2ead07bc39ea75a068'
    if not archive.is_file() or hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        raise RuntimeError('Exact libwmf source archive is required; see packaging/vendor-sources/README.md')
    if hashlib.sha256((SOURCES / 'libwmf-homebrew.patch').read_bytes()).hexdigest() != '80ae84a904baa21e1566e3d2bca1c6aaa0a2a30f684fe50f25e7e5751ef3ec93':
        raise RuntimeError('libwmf patch checksum differs from Homebrew recipe')
    with tarfile.open(archive) as tar:
        for entry in ('libwmf-0.2.13/fonts/LICENSE', 'libwmf-0.2.13/src/extra/gd/readme.txt'):
            try:
                member = tar.extractfile(entry)
            except KeyError:
                continue
            if member:
                (destination / 'libwmf' / entry.replace('/', '__')).write_bytes(member.read())
    ft_archive = SOURCES / 'freetype-2.13.3.tar.xz'
    if hashlib.sha256(ft_archive.read_bytes()).hexdigest() != '0550350666d427c74daeb85d5ac7bb353acba5f76956395995311a9c6f063289':
        raise RuntimeError('FreeType source checksum differs from the build recipe')
    with tarfile.open(ft_archive) as tar:
        for entry in ('docs/FTL.TXT', 'docs/GPLv2.TXT', 'src/bdf/README', 'src/pcf/README'):
            (destination / 'freetype' / entry.replace('/', '__')).write_bytes(tar.extractfile('freetype-2.13.3/' + entry).read())
    objc_archive = SOURCES / 'pyobjc_core-13.0.tar.gz'
    if hashlib.sha256(objc_archive.read_bytes()).hexdigest() != 'ffe154462d14aa4852f424e9116f17b5f8d44f78fc0f6c4727e721a5fa7177da':
        raise RuntimeError('PyObjC source checksum differs from PyPI')
    with tarfile.open(objc_archive) as tar:
        (destination / 'PyObjC-MIT-LICENSE.txt').write_bytes(tar.extractfile('pyobjc_core-13.0/License.txt').read())
    shutil.copytree(SOURCES, destination / 'libwmf/sources', dirs_exist_ok=True)
    shutil.copy2(ROOT / 'packaging/THIRD_PARTY_NOTICES.txt', destination / 'THIRD_PARTY_NOTICES.txt')
    (destination / 'python-packages.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2))
    # CPython's bundled license includes notices for its third-party modules.
    import sysconfig
    license_file = Path(sysconfig.get_path('stdlib')) / 'LICENSE.txt'
    if license_file.is_file():
        shutil.copy2(license_file, destination / 'CPython-LICENSE.txt')
    return inventory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=ROOT / 'dist' / 'desktop')
    parser.add_argument('--stage-only', action='store_true')
    parser.add_argument('--with-libreoffice', type=Path, help='Explicit optional LibreOfficeDev.app; adds about 408 MiB')
    args = parser.parse_args()
    if sys.platform != 'darwin':
        raise SystemExit('This build script is validated only for macOS. See Windows requirements document.')
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    build = ROOT / 'build' / 'desktop'
    os.environ['PYINSTALLER_CONFIG_DIR'] = str(build / 'pyinstaller-cache')
    stage = build / 'resources'
    if stage.exists():
        shutil.rmtree(stage)
    native = stage_wmf(stage / 'wmf')
    packages = stage_licenses(stage / 'licenses')
    optional_renderer = None
    if args.with_libreoffice:
        original = args.with_libreoffice.resolve()
        if not (original / 'Contents/MacOS/soffice').is_file() or not (original / 'Contents/Resources/LICENSE').is_file():
            raise RuntimeError('A complete LibreOffice app with runtime licenses is required')
        target = stage / 'libreoffice/LibreOfficeDev.app'
        shutil.copytree(original, target, symlinks=True)
        optional_renderer = {'source': str(original), 'bytes': sum(p.stat().st_size for p in target.rglob('*') if p.is_file() and not p.is_symlink()),
                             'soffice_sha256': hashlib.sha256((original / 'Contents/MacOS/soffice').read_bytes()).hexdigest(),
                             'status': 'optional original-WMF renderer; Windows unverified'}
    (stage / 'build-manifest.json').write_text(json.dumps({
        'platform': platform.platform(), 'architecture': platform.machine(),
        'python': sys.version, 'native': native, 'packages': packages,
        'optional_renderer': optional_renderer,
        'system_import': 'unverified', 'windows': 'unverified',
        'customer_distribution': 'pending signing and target-environment validation'
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.stage_only:
        print(stage)
        return
    icon = ROOT / 'assets' / 'icon.icns'
    if not icon.is_file() or icon.read_bytes()[:4] != b'icns':
        raise RuntimeError('A valid assets/icon.icns is required for the desktop release')
    manifest_path = stage / 'build-manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['application_name'] = APP_NAME
    manifest['icon_sha256'] = hashlib.sha256(icon.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    entry = build / 'desktop_entry.py'
    entry.write_text('from qa_parser.desktop_window import main\nif __name__ == "__main__":\n    raise SystemExit(main())\n')
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed', '--onedir',
               '--name', APP_NAME, '--icon', str(icon), '--osx-bundle-identifier', 'local.qa-parser.desktop',
               '--distpath', str(out), '--workpath', str(build / 'freeze'), '--specpath', str(build),
               '--paths', str(ROOT / 'src'), '--collect-data', 'qa_parser',
               '--hidden-import', 'webview.platforms.cocoa', '--hidden-import', 'webview.platforms.edgechromium']
    for excluded in ('PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'gi', 'webview.platforms.qt', 'webview.platforms.gtk', 'pytest', 'tkinter'):
        command += ['--exclude-module', excluded]
    command += [str(entry)]
    run(*command)
    app = out / (APP_NAME + '.app')
    plist_path = app / 'Contents/Info.plist'
    with plist_path.open('rb') as stream:
        plist = plistlib.load(stream)
    plist['CFBundleName'] = APP_NAME
    plist['CFBundleDisplayName'] = APP_NAME
    with plist_path.open('wb') as stream:
        plistlib.dump(plist, stream)
    packaged_icon = app / 'Contents/Resources' / plist.get('CFBundleIconFile', '')
    if not packaged_icon.is_file() or packaged_icon.read_bytes() != icon.read_bytes():
        raise RuntimeError('Bundled application icon does not match the approved icon asset')
    frameworks = app / 'Contents/Frameworks'
    for name in ('wmf', 'licenses'):
        shutil.copytree(stage / name, frameworks / name, dirs_exist_ok=True)
    if optional_renderer:
        shutil.copytree(stage / 'libreoffice', frameworks / 'libreoffice', symlinks=True)
    shutil.copy2(stage / 'build-manifest.json', frameworks / 'build-manifest.json')
    run('codesign', '--force', '--deep', '--sign', '-', app)
    run('codesign', '--verify', '--deep', '--strict', app)
    total = sum(p.stat().st_size for p in app.rglob('*') if p.is_file() and not p.is_symlink())
    print(json.dumps({'app': str(app), 'bytes': total, 'signature': 'ad-hoc; not notarized',
                      'application_name': plist['CFBundleDisplayName'],
                      'icon_file': plist['CFBundleIconFile'],
                      'icon_sha256': manifest['icon_sha256']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
