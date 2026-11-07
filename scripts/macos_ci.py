#!/usr/bin/env python3
"""Native Mac CI light application from SHA-pinned source dependencies.

Never installs or copies current Homebrew packages. Cache contains source archives
only; all hits are rehashed. Architecture is verified before and after freezing.
Existing scripts/build_desktop.py remains the separately validated local builder.
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
LOCK = ROOT / 'packaging/macos-runtime-lock.json'
APP_NAME = '题库转换'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(command, *, cwd=None, env=None, log=None):
    if log:
        with Path(log).open('ab') as stream:
            stream.write(('\nCOMMAND: ' + repr([str(v) for v in command]) + '\n').encode())
            result = subprocess.run([str(v) for v in command], cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT)
        if result.returncode:
            print(Path(log).read_text(encoding='utf-8', errors='replace')[-16000:], flush=True)
            raise RuntimeError(f'Command failed ({result.returncode}); see {log}')
    else:
        subprocess.run([str(v) for v in command], cwd=cwd, env=env, check=True)


def archive_for(item, cache):
    if item.get('vendored'):
        target = ROOT / item['vendored']
        if sha(target) != item['sha256']:
            raise RuntimeError(f'Vendored source checksum mismatch: {item["name"]}')
        return target
    target = cache / item['filename']
    if target.exists() and sha(target) == item['sha256']:
        return target
    temporary = target.with_suffix(target.suffix + '.partial')
    print('Downloading pinned source:', item['filename'], flush=True)
    run(['/usr/bin/curl', '-fL', '--retry', '3', '--retry-all-errors', '--connect-timeout', '20',
         '--max-time', '180', item['url'], '-o', temporary])
    if sha(temporary) != item['sha256']:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f'Download checksum mismatch: {item["filename"]}')
    temporary.replace(target)
    return target


def extract(archive, out):
    out.mkdir(parents=True)
    with tarfile.open(archive) as tar:
        tar.extractall(out, filter='data')
    roots = list(out.iterdir())
    if len(roots) != 1 or not roots[0].is_dir():
        raise RuntimeError(f'Expected one source root: {archive}')
    return roots[0]


def native_dependencies(build, arch, cache):
    lock = json.loads(LOCK.read_text(encoding='utf-8'))
    sources, archives = {}, {}
    for item in lock['sources']:
        archive = archive_for(item, cache)
        archives[item['name']] = archive
        sources[item['name']] = extract(archive, build / 'source' / item['name'])
    for patch in lock['patches']:
        path = ROOT / patch['path']
        if sha(path) != patch['sha256']:
            raise RuntimeError('Native patch checksum mismatch')
        run(['/usr/bin/patch', '-p1', '-i', path], cwd=sources['libwmf'], log=build / 'patch.log')
    prefix = build / 'native'
    prefix.mkdir()
    sdk = subprocess.check_output(['/usr/bin/xcrun', '--show-sdk-path'], text=True).strip()
    clang = subprocess.check_output(['/usr/bin/xcrun', '--find', 'clang'], text=True).strip()
    cmake = shutil.which('cmake')
    if not cmake:
        raise RuntimeError('Runner CMake is missing (build tool, never a customer prerequisite)')
    # Hide Homebrew pkg-config and unrelated native headers from configure.
    env = dict(os.environ, PATH=str(prefix / 'bin') + ':/usr/bin:/bin:/usr/sbin:/sbin',
               CC=clang, CXX=clang + '++', PKG_CONFIG='/usr/bin/false',
               LIBPNG_CFLAGS=f'-I{prefix}/include/libpng16', LIBPNG_LIBS=f'-L{prefix}/lib -lpng16 -lz',
               CPPFLAGS=f'-I{prefix}/include -isysroot {sdk}',
               CFLAGS=f'-O2 -arch {arch} -mmacosx-version-min=15.0',
               CXXFLAGS=f'-O2 -arch {arch} -mmacosx-version-min=15.0',
               LDFLAGS=f'-L{prefix}/lib -isysroot {sdk} -Wl,-headerpad_max_install_names',
               MACOSX_DEPLOYMENT_TARGET='15.0')
    common = [f'--prefix={prefix}', '--disable-static', '--enable-shared']
    configurations = {
        'libpng': common,
        'freetype': common + ['--enable-freetype-config', '--with-zlib=yes', '--with-bzip2=no', '--with-png=yes', '--with-harfbuzz=no', '--with-brotli=no'],
        'libwmf': common + ['--without-x', f'--with-freetype={prefix}', f'--with-png={prefix}', f'--with-jpeg={prefix}', '--with-expat', '--without-libxml2'],
    }
    for name in ('libpng', 'freetype', 'jpeg-turbo', 'libwmf'):
        source, log = sources[name], build / (name + '.log')
        print('Building pinned source:', name, arch, flush=True)
        if name == 'jpeg-turbo':
            run([cmake, '-S', source, '-B', source / 'build', f'-DCMAKE_INSTALL_PREFIX={prefix}',
                 f'-DCMAKE_OSX_ARCHITECTURES={arch}', '-DCMAKE_OSX_DEPLOYMENT_TARGET=15.0',
                 f'-DCMAKE_OSX_SYSROOT={sdk}', '-DCMAKE_INSTALL_LIBDIR=lib',
                 f'-DCMAKE_INSTALL_NAME_DIR={prefix}/lib', f'-DCMAKE_INSTALL_RPATH={prefix}/lib',
                 '-DWITH_JPEG8=1', '-DENABLE_STATIC=OFF', '-DWITH_SIMD=OFF'], env=env, log=log)
            run([cmake, '--build', source / 'build', '--parallel', '3'], env=env, log=log)
            run([cmake, '--install', source / 'build'], env=env, log=log)
        else:
            run([source / 'configure', *configurations[name]], cwd=source, env=env, log=log)
            run(['/usr/bin/make', '-j3'], cwd=source, env=env, log=log)
            run(['/usr/bin/make', 'install'], cwd=source, env=env, log=log)
    stage = build / 'resources'
    stage.mkdir()
    # Existing relocation algorithm, with the explicit CI prefix as only PATH.
    from build_desktop import stage_wmf
    old_path = os.environ.get('PATH', '')
    try:
        os.environ['PATH'] = str(prefix / 'bin') + ':/usr/bin:/bin:/usr/sbin:/sbin'
        native = stage_wmf(stage / 'wmf')
    finally:
        os.environ['PATH'] = old_path
    for entry in native:
        original = Path(entry['source'])
        if not original.is_relative_to(prefix):
            raise RuntimeError(f'Unexpected non-system native dependency: {original}')
    licenses = stage / 'licenses'
    (licenses / 'sources').mkdir(parents=True)
    for item in lock['sources']:
        shutil.copy2(archives[item['name']], licenses / 'sources' / archives[item['name']].name)
        output = licenses / item['name']
        output.mkdir()
        for file in sources[item['name']].rglob('*'):
            if file.is_file() and re.match(r'^(license|copying|notice|authors|ftl\.txt|gplv2\.txt)', file.name, re.I):
                target = output / file.relative_to(sources[item['name']])
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file, target)
    for patch in lock['patches']:
        shutil.copy2(ROOT / patch['path'], licenses / 'sources')
    shutil.copy2(LOCK, licenses)
    shutil.copy2(ROOT / 'packaging/macos-THIRD-PARTY-NOTICES.txt', licenses)
    return stage, native


def package_licenses(destination):
    destination.mkdir()
    inventory = []
    for dist in metadata.distributions():
        name = dist.metadata['Name']
        if name.lower() == 'qa-parser':
            continue
        files = []
        for entry in dist.files or []:
            if any(re.match(r'^(license|copying|notice|authors)', part, re.I) for part in Path(entry).parts):
                original = Path(dist.locate_file(entry))
                if original.is_file():
                    output = destination / name / str(entry).replace('/', '__')
                    output.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(original, output)
                    files.append(output.name)
        inventory.append({'name': name, 'version': dist.version, 'license_files': files})
    shutil.copy2(ROOT / 'packaging/vendor-sources/proxy_tools-LICENSE.txt', destination)
    archive = ROOT / 'packaging/vendor-sources/pyobjc_core-13.0.tar.gz'
    if sha(archive) != 'ffe154462d14aa4852f424e9116f17b5f8d44f78fc0f6c4727e721a5fa7177da':
        raise RuntimeError('PyObjC source license archive checksum mismatch')
    with tarfile.open(archive) as tar:
        (destination / 'PyObjC-MIT-LICENSE.txt').write_bytes(tar.extractfile('pyobjc_core-13.0/License.txt').read())
    import sysconfig
    candidates = [Path(sysconfig.get_path('stdlib')) / 'LICENSE.txt', Path(sys.base_prefix) / 'LICENSE.txt']
    license_file = next((p for p in candidates if p.is_file()), None)
    if license_file is None:
        raise RuntimeError('CPython license not found')
    shutil.copy2(license_file, destination / 'CPython-LICENSE.txt')
    (destination / 'python-packages.json').write_text(json.dumps(inventory, indent=2), encoding='utf-8')
    return inventory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--architecture', choices=['arm64', 'x86_64'], required=True)
    parser.add_argument('--cache', type=Path, default=Path.home() / 'Library/Caches/qa-parser-macos/downloads')
    args = parser.parse_args()
    if sys.platform != 'darwin' or platform.machine() != args.architecture:
        raise RuntimeError('Must build natively on the requested Mac architecture; Rosetta/cross-builds are not accepted')
    build = ROOT / 'build/macos-ci'
    out = ROOT / 'dist/macos'
    if build.exists() or out.exists():
        raise RuntimeError('Use fresh build/macos-ci and dist/macos directories')
    build.mkdir(parents=True)
    out.mkdir(parents=True)
    args.cache.mkdir(parents=True, exist_ok=True)
    stage, native = native_dependencies(build, args.architecture, args.cache)
    packages = package_licenses(stage / 'licenses/python')
    os.environ['PYINSTALLER_CONFIG_DIR'] = str(build / 'pyinstaller-cache')
    entry = build / 'desktop_entry.py'
    entry.write_text('from qa_parser.desktop_window import main\nif __name__ == "__main__":\n    raise SystemExit(main())\n')
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed', '--onedir',
               '--name', APP_NAME, '--icon', str(ROOT / 'assets/icon.icns'),
               '--osx-bundle-identifier', 'local.qa-parser.desktop', '--target-arch', args.architecture,
               '--distpath', str(out), '--workpath', str(build / 'freeze'), '--specpath', str(build),
               '--paths', str(ROOT / 'src'), '--collect-data', 'qa_parser', '--hidden-import', 'webview.platforms.cocoa']
    for excluded in ('PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'gi', 'webview.platforms.qt', 'webview.platforms.gtk', 'webview.platforms.winforms', 'pytest', 'tkinter'):
        command += ['--exclude-module', excluded]
    run(command + [entry], log=build / 'pyinstaller.log')
    app = out / (APP_NAME + '.app')
    framework = app / 'Contents/Frameworks'
    for name in ('wmf', 'licenses'):
        shutil.copytree(stage / name, framework / name)
    plist_file = app / 'Contents/Info.plist'
    with plist_file.open('rb') as stream:
        plist = plistlib.load(stream)
    plist['CFBundleName'] = plist['CFBundleDisplayName'] = APP_NAME
    plist['LSMinimumSystemVersion'] = '15.0'
    with plist_file.open('wb') as stream:
        plistlib.dump(plist, stream)
    manifest = {'schema_version': 1, 'platform': 'darwin', 'architecture': args.architecture,
                'minimum_os': '15.0', 'python': sys.version, 'native': native, 'packages': packages,
                'source_commit': os.environ.get('GITHUB_SHA'), 'runtime_lock_sha256': sha(LOCK),
                'toolchain': {'clang': subprocess.check_output(['/usr/bin/xcrun', 'clang', '--version'], text=True).strip(),
                              'cmake': subprocess.check_output([shutil.which('cmake'), '--version'], text=True).strip(),
                              'sdk': subprocess.check_output(['/usr/bin/xcrun', '--show-sdk-version'], text=True).strip()},
                'variant': 'light', 'real_reference_validation': 'not performed; private corpus not uploaded',
                'customer_distribution': 'pending signing/notarization and target-environment validation'}
    (framework / 'build-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    run(['/usr/bin/codesign', '--force', '--deep', '--sign', '-', app])
    run(['/usr/bin/codesign', '--verify', '--deep', '--strict', app])
    files = [{'path': p.relative_to(framework).as_posix(), 'bytes': p.stat().st_size, 'sha256': sha(p)}
             for name in ('wmf', 'licenses') for p in sorted((framework / name).rglob('*')) if p.is_file()]
    resources = {'schema_version': 1, 'platform': 'darwin', 'architecture': args.architecture, 'files': files}
    # Manifest addition requires signing again but does not mutate native content.
    (framework / 'resources-manifest.json').write_text(json.dumps(resources, indent=2), encoding='utf-8')
    run(['/usr/bin/codesign', '--force', '--sign', '-', app])
    run(['/usr/bin/codesign', '--verify', '--deep', '--strict', app])
    (build / 'build-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    (build / 'resources-manifest.json').write_text(json.dumps(resources, indent=2), encoding='utf-8')
    print(app)


if __name__ == '__main__':
    main()
