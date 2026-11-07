#!/usr/bin/env python3
"""Stage SHA-pinned Windows runtimes; never invoked by the customer program."""
from __future__ import annotations
import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / 'packaging/windows-runtime-lock.json'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download(item, cache):
    path = cache / item['filename']
    if path.is_file() and sha(path) == item['sha256']:
        return path
    temporary = path.with_suffix(path.suffix + '.partial')
    # curl is a build-host tool on Windows Server; TLS validation remains on.
    subprocess.run(['curl.exe' if sys.platform == 'win32' else 'curl', '-fL', '--retry', '3',
                    '--connect-timeout', '30', '--max-time', '600', item['url'], '-o', str(temporary)], check=True)
    if sha(temporary) != item['sha256']:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"Checksum mismatch: {item['filename']}")
    temporary.replace(path)
    return path


def unpack_package(archive, target):
    import zstandard
    with archive.open('rb') as compressed, zstandard.ZstdDecompressor().stream_reader(compressed) as raw:
        with tarfile.open(fileobj=raw, mode='r|') as tar:
            for entry in tar:
                name = PurePosixPath(entry.name)
                if name.is_absolute() or '..' in name.parts:
                    raise RuntimeError('Unsafe package path')
                if not entry.isfile():
                    continue  # no links, devices or executables outside explicit copy set
                dest = target.joinpath(*name.parts)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(entry) as src, dest.open('wb') as dst:
                    shutil.copyfileobj(src, dst)


def python_licenses(target):
    target.mkdir(parents=True, exist_ok=True)
    inventory = []
    for dist in metadata.distributions():
        name = dist.metadata['Name']
        if name.lower() == 'qa-parser':
            continue
        copied = []
        for entry in dist.files or []:
            if any(re.match(r'^(license|copying|notice|authors)', part, re.I) for part in Path(entry).parts):
                source = Path(dist.locate_file(entry))
                if source.is_file():
                    dest = target / name / str(entry).replace('/', '__').replace('\\', '__')
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, dest)
                    copied.append(dest.name)
        inventory.append({'name': name, 'version': dist.version, 'license_files': copied})
    shutil.copy2(ROOT / 'packaging/vendor-sources/proxy_tools-LICENSE.txt', target / 'proxy_tools-LICENSE.txt')
    for candidate in [Path(sys.base_prefix) / 'LICENSE.txt', Path(sys.base_prefix) / 'Lib/LICENSE.txt']:
        if candidate.is_file():
            shutil.copy2(candidate, target / 'CPython-LICENSE.txt')
            break
    else:
        raise RuntimeError('CPython license not found')
    (target / 'python-packages.json').write_text(json.dumps(inventory, indent=2), encoding='utf-8')


def stage(out: Path, cache: Path):
    if sys.platform != 'win32':
        raise RuntimeError('Runtime staging requires native Windows x64')
    if out.exists():
        raise RuntimeError(f'Resource destination already exists: {out}')
    out.mkdir(parents=True)
    cache.mkdir(parents=True, exist_ok=True)
    lock = json.loads(LOCK.read_text(encoding='utf-8'))
    licenses = out / 'licenses'
    licenses.mkdir()
    cab = download(lock['webview2'], cache)
    with tempfile.TemporaryDirectory(prefix='qa-webview2-') as temp:
        subprocess.run(['expand.exe', str(cab), '-F:*', temp], check=True)
        candidates = list(Path(temp).rglob('msedgewebview2.exe'))
        if len(candidates) != 1:
            raise RuntimeError('Fixed WebView2 package root is ambiguous')
        shutil.copytree(candidates[0].parent, out / 'webview2')
    # Keep the Microsoft runtime whole, including third-party notices/resources.
    with tempfile.TemporaryDirectory(prefix='qa-wmf-') as temp:
        staging = Path(temp)
        for item in lock['wmf']['packages']:
            archive = download(item, cache)
            unpack_package(archive, staging / item['name'])
        wmf = out / 'wmf'
        (wmf / 'bin').mkdir(parents=True)
        for filename, owner in lock['wmf']['files'].items():
            source = staging / owner / 'ucrt64/bin' / filename
            if not source.is_file():
                raise RuntimeError(f'Missing pinned PE dependency: {filename}')
            shutil.copy2(source, wmf / 'bin' / filename)
        font_source = staging / lock['wmf']['root_package'] / 'ucrt64/share/libwmf/fonts'
        shutil.copytree(font_source, wmf / 'fonts')
        fontmap = wmf / 'fonts/fontmap'
        fontmap.write_text(re.sub(r'="[^"\n]*/([^"/]+\.(?:afm|t1))"', r'="\1"',
                                 fontmap.read_text(encoding='utf-8')), encoding='utf-8')
        for item in lock['wmf']['packages']:
            package_root = staging / item['name']
            dest = licenses / item['name']
            dest.mkdir()
            for name in ('.PKGINFO', '.BUILDINFO'):
                if (package_root / name).is_file():
                    shutil.copy2(package_root / name, dest / name.lstrip('.'))
            upstream = package_root / 'ucrt64/share/licenses'
            if upstream.exists():
                shutil.copytree(upstream, dest / 'notices')
        (licenses / 'sources').mkdir()
        for item in lock['wmf']['sources']:
            shutil.copy2(download(item, cache), licenses / 'sources' / item['filename'])
    python_licenses(licenses / 'python')
    shutil.copy2(LOCK, licenses / LOCK.name)
    shutil.copy2(ROOT / 'packaging/WINDOWS-THIRD-PARTY-NOTICES.txt', licenses)
    files = [{'path': p.relative_to(out).as_posix(), 'bytes': p.stat().st_size, 'sha256': sha(p)}
             for p in sorted(out.rglob('*')) if p.is_file()]
    manifest = {'schema_version': 1, 'platform': 'win32', 'architecture': 'x64',
                'runtime_lock_sha256': sha(LOCK), 'files': files,
                'real_reference_validation': 'not run; no customer documents uploaded',
                'libreoffice': 'not included; Windows font isolation unverified'}
    (out / 'resources-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps({'files': len(files), 'bytes': sum(v['bytes'] for v in files)}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=ROOT / 'build/windows/resources')
    parser.add_argument('--cache', type=Path, default=ROOT / 'build/windows/downloads')
    args = parser.parse_args()
    stage(args.out.resolve(), args.cache.resolve())


if __name__ == '__main__':
    main()
