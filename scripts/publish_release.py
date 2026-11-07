#!/usr/bin/env python3
"""Publish a new draft only after every expected asset has matching server SHA.

Called exclusively by the tag release job. Never updates/replaces an existing
release; failed upload/verification leaves an unpublished draft for inspection.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def gh(*args):
    return subprocess.check_output(['gh', *map(str, args)], text=True)


def publish(directory, repository):
    manifest = json.loads((directory / 'release-manifest.json').read_text(encoding='utf-8'))
    tag = manifest['tag']
    files = sorted(p for p in directory.iterdir() if p.is_file())
    command = ['release', 'create', tag, '--repo', repository, '--verify-tag', '--draft',
               '--title', f'题库转换 {tag}', '--notes-file', str(directory / 'RELEASE_NOTES.md')]
    if manifest['prerelease']:
        command.append('--prerelease')
    gh(*command, *files)
    release = json.loads(gh('api', f'repos/{repository}/releases/tags/{tag}'))
    if not release['draft']:
        raise RuntimeError('Expected an unpublished draft')
    assets = {a['name']: a for a in release['assets']}
    if set(assets) != {p.name for p in files}:
        raise RuntimeError('Incomplete release asset set; draft retained')
    for path in files:
        with path.open('rb') as stream:
            sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        item = assets[path.name]
        if item['size'] != path.stat().st_size or item.get('digest') != 'sha256:' + sha:
            raise RuntimeError(f'Remote asset digest/size mismatch: {path.name}; draft retained')
    gh('release', 'edit', tag, '--repo', repository, '--draft=false',
       '--prerelease=' + str(manifest['prerelease']).lower())


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--repo', required=True)
    a = p.parse_args()
    publish(a.directory, a.repo)
