#!/usr/bin/env python3
"""Delete only an owned, completed candidate after all assets have public SHA proof.

No tag, source or published Release deletion. A persisted receipt is required;
re-running after a successful deletion is harmless. Never accepts arbitrary URLs.
"""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from release_draft import api, assets, TARGETS
from sync_release_oss import release_owner, verify_public


def cleanup(repo, release_id, receipt, *, delete=False):
    if repo != 'javen-yan/qa-parser' or not re.fullmatch(r'[1-9][0-9]*', str(release_id)):
        raise ValueError('Cleanup is limited to this project and numeric release IDs')
    if receipt.get('repository') != repo or str(receipt.get('release_id')) != str(release_id):
        raise ValueError('Receipt repository/release mismatch')
    if receipt.get('status') != 'uploaded-and-public-sha256-verified' or not receipt.get('draft'):
        raise ValueError('Complete public download SHA evidence for a draft is required')
    try:
        release = api(f'repos/{repo}/releases/{release_id}')
    except RuntimeError as error:
        if 'HTTP 404' in str(error):
            return {'status': 'already-absent', 'release_id': str(release_id)}
        raise
    owner, prefix = release_owner(repo, release)
    if not release['draft'] or owner['mode'] != 'manual' or owner['commit'] != receipt['commit']:
        raise ValueError('Only the exact manual candidate draft may be deleted')
    run = api(f"repos/{repo}/actions/runs/{owner['run_id']}")
    if run.get('status') != 'completed' or run.get('head_sha') != owner['commit'] or run.get('path') != '.github/workflows/release.yml':
        raise ValueError('Producer workflow is still running or source identity differs')
    current = assets(repo, release_id)
    expected = {'release-manifest.json', 'SHA256SUMS.txt', 'RELEASE_NOTES.md'}
    for target in TARGETS:
        pair = {f'qa-parser-{target}-candidate.zip', f'release-metadata-{target}.json'}
        if pair & set(current) and not pair <= set(current):
            raise ValueError('Candidate archive/metadata pair is incomplete')
        expected |= pair
    if set(current) - expected:
        raise ValueError('Unexpected assets in draft; retaining it')
    mirrored = {}
    for row in receipt['targets']:
        for item in row.get('uploads', []):
            name = item['key'].rsplit('/', 1)[-1]
            if name in current:
                mirrored[name] = item
    # Common finalization assets can be mirrored by the caller in their own
    # release/ subdirectory, using the same immutable upload/full GET checks.
    for item in receipt.get('release_uploads', []):
        mirrored[item['key'].rsplit('/', 1)[-1]] = item
    if not current or set(mirrored) != set(current):
        raise ValueError('Not every Release asset is present in the verified OSS receipt')
    for name, item in mirrored.items():
        remote = current[name]
        if remote.get('state') != 'uploaded' or remote.get('digest') != 'sha256:' + item['sha256'] or remote.get('size') != item['bytes']:
            raise ValueError('Release asset changed since OSS verification')
        url = urlsplit(item['url'])
        if url.scheme != 'https' or url.netloc != 'maiya-reader.oss-cn-beijing.aliyuncs.com' or url.username or url.password or url.query or url.fragment or url.path != '/' + item['key'] or not item['key'].startswith(prefix + '/'):
            raise ValueError('Receipt URL is outside the authorized candidate destination')
        verify_public(item['url'], item['bytes'], item['sha256'])
    # Recheck after the potentially long downloads, immediately before deletion.
    latest = api(f'repos/{repo}/releases/{release_id}')
    latest_owner, _ = release_owner(repo, latest)
    if not latest.get('draft') or latest_owner != owner or assets(repo, release_id) != current:
        raise ValueError('Draft changed during verification; retaining it')
    if api(f"repos/{repo}/actions/runs/{owner['run_id']}").get('status') != 'completed':
        raise ValueError('Producer restarted during verification; retaining draft')
    if delete:
        api(f'repos/{repo}/releases/{release_id}', method='DELETE')
    return {'status': 'deleted' if delete else 'verified-ready-to-delete',
            'release_id': str(release_id), 'commit': owner['commit'], 'assets': sorted(current)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default='javen-yan/qa-parser')
    parser.add_argument('--release-id', required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--delete', action='store_true')
    args = parser.parse_args()
    result = cleanup(args.repo, args.release_id, json.loads(args.receipt.read_text()), delete=args.delete)
    args.receipt.with_name('draft-cleanup-result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))

if __name__ == '__main__':
    main()
