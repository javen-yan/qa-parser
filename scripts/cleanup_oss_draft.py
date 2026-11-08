#!/usr/bin/env python3
"""Retain a candidate Release with OSS links; remove only verified mirrored assets."""
from __future__ import annotations
import argparse
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit
from release_draft import api, assets, TARGETS
from sync_release_oss import release_owner, verify_public


def retry_read(operation, *args):
    for attempt in range(3):
        try:
            return operation(*args)
        except RuntimeError as error:
            transient = any(value in str(error) for value in (
                'TLS handshake timeout', 'EOF', 'connection reset', 'HTTP 502', 'HTTP 503', 'HTTP 504'))
            if not transient or attempt == 2:
                raise
            time.sleep(attempt + 1)


def read_api(endpoint):
    return retry_read(api, endpoint)


def read_assets(repo, release_id):
    return retry_read(assets, repo, release_id)


def asset_identity(item):
    # Draft description updates can regenerate browser_download_url; download
    # counters also change on reads. Immutable identity and content must match.
    return {k: item.get(k) for k in ('id', 'name', 'state', 'size', 'digest')}


def cleanup(repo, release_id, receipt, *, apply=False):
    if repo != 'javen-yan/qa-parser' or not re.fullmatch(r'[1-9][0-9]*', str(release_id)):
        raise ValueError('Cleanup is limited to this project and numeric release IDs')
    if receipt.get('repository') != repo or str(receipt.get('release_id')) != str(release_id):
        raise ValueError('Receipt repository/release mismatch')
    if receipt.get('status') != 'uploaded-and-public-sha256-verified':
        raise ValueError('Complete public download SHA evidence for a candidate is required')
    release = read_api(f'repos/{repo}/releases/{release_id}')
    marker = f"<!-- qa-parser-oss-delivery:{release_id}:{receipt['commit']} -->"
    owner, prefix = release_owner(repo, dict(release, draft=True))
    formal = owner['mode'] == 'tag'
    prerelease = '-' in owner['tag'] if formal else True
    if owner['commit'] != receipt['commit']:
        raise ValueError('Release and receipt source differ')
    if formal and {row['target'] for row in receipt['targets']} != set(TARGETS):
        raise ValueError('Formal release requires all three verified targets')
    if not formal and not release['draft'] and (not release.get('prerelease') or marker not in release.get('body', '')):
        raise ValueError('Never modify an unrelated published Release')
    def check_run():
        run = read_api(f"repos/{repo}/actions/runs/{owner['run_id']}")
        if run.get('status') != 'completed' or run.get('head_sha') != owner['commit'] or run.get('path') != '.github/workflows/release.yml' or (formal and run.get('conclusion') != 'success'):
            raise ValueError('Producer workflow is running or source identity differs')
    check_run()
    def check_source_tag(required):
        try:
            item = read_api(f"repos/{repo}/git/ref/tags/{owner['tag']}")['object']
        except RuntimeError as error:
            if not required and 'HTTP 404' in str(error):
                return
            raise
        for _ in range(8):
            if item['type'] == 'commit':
                if item['sha'] != owner['commit']:
                    raise ValueError('Candidate Source tag differs from verified commit')
                return
            if item['type'] != 'tag':
                break
            item = read_api(f"repos/{repo}/git/tags/{item['sha']}")['object']
        raise ValueError('Candidate Source tag cannot be resolved')
    if apply:
        check_source_tag(not release['draft'])
    expected = {'release-manifest.json', 'SHA256SUMS.txt', 'RELEASE_NOTES.md'}
    for target in TARGETS:
        expected |= {f'qa-parser-{target}-candidate.zip', f'release-metadata-{target}.json'}
        if formal:
            expected.add(f"qa-parser-{owner['tag']}-{target}-light.zip")
    mirrored = {}
    all_objects = []
    for row in receipt['targets']:
        all_objects.extend(row.get('uploads', []))
    all_objects.extend(receipt.get('release_uploads', []))
    for item in all_objects:
        name = item['key'].rsplit('/', 1)[-1]
        is_source_asset = name.endswith(('-candidate.zip', '-light.zip')) or name.startswith('release-metadata-') or item in receipt.get('release_uploads', [])
        if name in expected and is_source_asset:
            if name in mirrored and mirrored[name] != item:
                raise ValueError('Ambiguous mirror evidence')
            mirrored[name] = item
        url = urlsplit(item['url'])
        if url.scheme != 'https' or url.netloc != 'maiya-reader.oss-cn-beijing.aliyuncs.com' or url.username or url.password or url.query or url.fragment or url.path != '/' + item['key'] or not item['key'].startswith(prefix + '/'):
            raise ValueError('Receipt URL is outside the authorized destination')
        verify_public(item['url'], item['bytes'], item['sha256'])
    if formal:
        for row in receipt['targets']:
            mirrored[f"qa-parser-{row['target']}-candidate.zip"] = mirrored[row['filename']]
    if not any(name.endswith('-candidate.zip') for name in mirrored):
        raise ValueError('No candidate archive evidence')
    for target in TARGETS:
        pair = {f'qa-parser-{target}-candidate.zip', f'release-metadata-{target}.json'}
        if pair & set(mirrored) and not pair <= set(mirrored):
            raise ValueError('Candidate mirror pair is incomplete')
    if formal and not {'release-manifest.json', 'SHA256SUMS.txt', 'RELEASE_NOTES.md'} <= set(mirrored):
        raise ValueError('Formal Release requires complete mirrored manifest and notes')
    current = read_assets(repo, release_id)
    if set(current) - set(mirrored):
        raise ValueError('Not every Release asset has verified OSS evidence')
    for name, remote in current.items():
        item = mirrored[name]
        if remote.get('state') != 'uploaded' or remote.get('digest') != 'sha256:' + item['sha256'] or remote.get('size') != item['bytes']:
            raise ValueError('Release asset changed since OSS verification')
    # Strip only our own prior mirror section, leaving the ownership marker and
    # original candidate description intact. This makes interrupted retries safe.
    body = release.get('body', '').split(marker)[0].rstrip()
    body = body.replace('此为手动草稿，不指定正式版本，不自动发布。', '此为候选预发布，不指定稳定正式版本。')
    label = owner['tag'] if formal else '候选测试版'
    body += '\n\n' + marker + f'\n\n## 阿里云下载（{label}）\n\n'
    body += '完整解压后运行：Windows 打开 EXE，Mac 打开 .app。保留运行时和许可证。\n\n'
    body += '| 平台 | 下载 | 字节数 | SHA-256 |\n|---|---|---:|---|\n'
    for row in receipt['targets']:
        archive = mirrored[row['filename']]
        body += f"| {row['target']} | [下载 ZIP]({archive['url']}) | {archive['bytes']} | `{archive['sha256']}` |\n"
    body += '\n校验和、说明和构建记录：\n\n'
    for item in all_objects:
        if not item['key'].endswith('.zip'):
            body += f"- [{item['key'].rsplit('/', 1)[-1]}]({item['url']})\n"
    body += '\n' + ('正式发布。' if not prerelease else '这是候选预发布，不是稳定正式版。') + '客户环境、签名公证及真实系统导入尚未验收。Source 链接保留对应源码。\n'
    latest = read_api(f'repos/{repo}/releases/{release_id}')
    release_fields = ('body', 'draft', 'prerelease', 'tag_name', 'target_commitish')
    if any(latest.get(k) != release.get(k) for k in release_fields) or {n: asset_identity(a) for n, a in read_assets(repo, release_id).items()} != {n: asset_identity(a) for n, a in current.items()}:
        raise ValueError('Release changed during verification; retaining attachments')
    check_run()
    removed = []
    if apply:
        payload = {'body': body, 'tag_name': owner['tag'], 'target_commitish': owner['commit'],
                   'draft': release['draft'], 'prerelease': prerelease, 'make_latest': 'true' if not prerelease else 'false'}
        api(f'repos/{repo}/releases/{release_id}', method='PATCH', payload=payload)
        saved = read_api(f'repos/{repo}/releases/{release_id}')
        if saved.get('body') != body or saved.get('tag_name') != owner['tag'] or saved.get('target_commitish') != owner['commit']:
            raise ValueError('OSS download description not saved; attachments retained')
        for name, remote in current.items():
            fresh = read_assets(repo, release_id)
            if name not in fresh or asset_identity(fresh[name]) != asset_identity(remote) or set(fresh) - set(current):
                raise ValueError('Attachments changed; stopping cleanup')
            check_run()
            api(f"repos/{repo}/releases/assets/{remote['id']}", method='DELETE')
            removed.append(name)
        if read_assets(repo, release_id):
            raise ValueError('Attachments remain; keep candidate state')
        result = api(f'repos/{repo}/releases/{release_id}', method='PATCH', payload={
            'body': body, 'tag_name': owner['tag'], 'target_commitish': owner['commit'],
            'draft': False, 'prerelease': prerelease, 'make_latest': 'true' if not prerelease else 'false'})
        if result.get('draft') or result.get('prerelease') != prerelease or result.get('body') != body or result.get('tag_name') != owner['tag']:
            raise ValueError('Candidate publication state mismatch')
        check_source_tag(True)
        url = result['html_url']
    else:
        url = release.get('html_url')
    return {'status': 'release-retained-with-oss-links' if apply else 'verified-ready-to-retain-release',
            'release_id': str(release_id), 'commit': owner['commit'], 'removed_assets': removed, 'release_url': url}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default='javen-yan/qa-parser')
    parser.add_argument('--release-id', required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    result = cleanup(args.repo, args.release_id, json.loads(args.receipt.read_text()), apply=args.apply)
    args.receipt.with_name('release-archive-result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))

if __name__ == '__main__':
    main()
