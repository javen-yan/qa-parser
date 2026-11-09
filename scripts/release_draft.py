#!/usr/bin/env python3
"""Transfer native candidates through an owned draft, without Actions artifacts.

Never replaces assets, adopts another run's draft or publishes a manual candidate.
GitHub CLI supplies authentication from GH_TOKEN; tokens never enter argv or files.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import tomllib
from urllib.parse import quote

from prepare_release import ROOT, assemble, validate_candidates

TARGETS = ('windows-x64', 'macos-arm64', 'macos-x86_64')
PREFIX = '<!-- qa-parser-release-owner:'
API_VERSION = '2026-03-10'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def api(endpoint, *, method='GET', payload=None, file=None, content_type=None, output=None):
    args = ['gh', 'api', endpoint, '--method', method, '-H', f'X-GitHub-Api-Version: {API_VERSION}']
    if output is not None:
        args += ['-H', 'Accept: application/octet-stream']
    if file is not None:
        args += ['--input', str(file), '-H', 'Content-Type: ' + (content_type or 'application/octet-stream')]
    elif payload is not None:
        args += ['--input', '-']
    data = json.dumps(payload).encode() if payload is not None else None
    if output is not None:
        with output.open('wb') as stream:
            result = subprocess.run(args, input=data, stdout=stream, stderr=subprocess.PIPE)
    else:
        result = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError(f'GitHub {method} failed ({result.returncode}): {result.stderr.decode(errors="replace")[-1500:]}')
    return None if output is not None else json.loads(result.stdout or b'null')


def identity(repo, run_id, commit):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('Invalid repository')
    if not re.fullmatch(r'[1-9][0-9]*', run_id) or not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('Expected numeric workflow run id and full commit SHA')
    return {'schema_version': 1, 'repository': repo, 'run_id': run_id, 'commit': commit}


def marker(owner):
    return PREFIX + json.dumps(owner, sort_keys=True, separators=(',', ':')) + ' -->'


def owned(repo, release_id, owner):
    if not str(release_id).isdigit():
        raise ValueError('Invalid release id')
    release = api(f'repos/{repo}/releases/{release_id}')
    if not release.get('draft') or release.get('published_at'):
        raise ValueError('Refusing to change a published release')
    matches = re.findall(re.escape(PREFIX) + r'(.*?) -->', release.get('body') or '')
    if len(matches) != 1:
        raise ValueError('Draft ownership marker missing or ambiguous')
    saved = json.loads(matches[0])
    for key, value in owner.items():
        if saved.get(key) != value:
            raise ValueError(f'Draft belongs to another run/source: {key}')
    if saved.get('mode') not in ('manual', 'tag') or release['tag_name'] != saved.get('tag'):
        raise ValueError('Draft mode/tag identity mismatch')
    if release.get('target_commitish') != owner['commit']:
        raise ValueError('Draft target commit changed')
    return release, saved


def assets(repo, release_id):
    result = []
    for page in range(1, 101):
        batch = api(f'repos/{repo}/releases/{release_id}/assets?per_page=100&page={page}')
        result.extend(batch)
        if len(batch) < 100:
            break
    else:
        raise ValueError('Too many assets')
    names = [v['name'] for v in result]
    if len(set(names)) != len(names):
        raise ValueError('Duplicate release asset names')
    return {v['name']: v for v in result}


def check_asset(path, item):
    if item.get('state') != 'uploaded' or item['size'] != path.stat().st_size or item.get('digest') != 'sha256:' + sha(path):
        raise ValueError(f'Server digest/state/size mismatch: {path.name}; draft retained')


def upload(repo, release_id, owner, path, name=None):
    owned(repo, release_id, owner)
    name = name or path.name
    if Path(name).name != name or not re.fullmatch(r'[A-Za-z0-9_.-]+', name):
        raise ValueError('Unsafe asset name')
    if not path.is_file() or path.stat().st_size >= 2 * 1024**3:
        raise ValueError('Asset missing or exceeds GitHub per-file size limit')
    current = assets(repo, release_id)
    if name in current:
        check_asset(path, current[name])  # exact retry is allowed; never overwrite
        return
    result = api(f'https://uploads.github.com/repos/{repo}/releases/{release_id}/assets?name={quote(name)}',
                 method='POST', file=path,
                 content_type='application/zip' if name.endswith('.zip') else 'application/octet-stream')
    check_asset(path, result)


def check_tag_commit(repo, tag, commit):
    ref = api(f'repos/{repo}/git/ref/tags/{quote(tag, safe="")}')
    item = ref['object']
    for _ in range(8):
        if item['type'] == 'commit':
            if item['sha'] != commit:
                raise ValueError('User tag no longer points to the checked source commit')
            return
        if item['type'] != 'tag':
            raise ValueError('Tag does not resolve to a commit')
        item = api(f"repos/{repo}/git/tags/{item['sha']}")['object']
    raise ValueError('Annotated tag chain exceeds limit')


def prepare(repo, owner, mode, tag):
    if mode == 'manual':
        tag = f"candidate-{owner['commit'][:12]}-{owner['run_id']}"
    else:
        if not re.fullmatch(r'v\d+\.\d+\.\d+(?:-(?:rc|beta|alpha)\.\d+)?', tag):
            raise ValueError('Tag must be user-created vX.Y.Z[-rc.N/-beta.N/-alpha.N]')
        version = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
        if tag[1:].split('-')[0] != version:
            raise ValueError('User tag does not match the project version')
        policy = json.loads((ROOT / 'packaging/release-policy.json').read_text())
        if '-' not in tag and not policy['stable_release_approved']:
            raise ValueError('Stable release not approved by version-controlled policy')
        # Tag-triggered publication may not create or retarget an absent tag.
        check_tag_commit(repo, tag, owner['commit'])
    for page in range(1, 101):
        entries = api(f'repos/{repo}/releases?per_page=100&page={page}')
        if any(v['tag_name'] == tag for v in entries):
            raise ValueError('Release already exists; will not replace or adopt it')
        if len(entries) < 100:
            break
    else:
        raise ValueError('Release inventory exceeds bounded scan')
    owner = dict(owner, mode=mode, tag=tag)
    release = api(f'repos/{repo}/releases', method='POST', payload={
        'tag_name': tag, 'target_commitish': owner['commit'], 'name': f'题库转换 {tag}',
        'draft': True, 'prerelease': True,
        'body': marker(owner) + '\n\n构建中：尚未完成三个原生平台检查。不要将部分资产视为完整交付。\n'})
    if not release.get('draft'):
        raise ValueError('GitHub did not create the requested draft')
    outputs = {'release_id': str(release['id']), 'tag': tag, 'mode': mode, 'url': release['html_url']}
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as f:
            for key, value in outputs.items():
                f.write(f'{key}={value}\n')
    print(json.dumps(outputs))
    return outputs


def upload_candidate(repo, release_id, owner, target, archive, metadata):
    if target not in TARGETS or archive.name != f'qa-parser-{target}-candidate.zip':
        raise ValueError('Unexpected candidate target/file')
    data = json.loads(metadata.read_text(encoding='utf-8'))
    if not (data.get('status') == 'passed' and data.get('target') == target and
            data.get('commit') == owner['commit'] and data.get('zip_sha256') == sha(archive) and
            data.get('native_smoke_passed') and data.get('source_closure_complete') and data.get('variant') == 'light'):
        raise ValueError('Native candidate metadata gate failed')
    upload(repo, release_id, owner, archive)
    upload(repo, release_id, owner, metadata, f'release-metadata-{target}.json')


def finalize(repo, release_id, owner, work, *, keep_draft=False, assembler=None):
    release, saved = owned(repo, release_id, owner)
    current = assets(repo, release_id)
    candidate_names = {f'qa-parser-{target}-candidate.zip' for target in TARGETS}
    candidate_names |= {f'release-metadata-{target}.json' for target in TARGETS}
    if not candidate_names.issubset(current):
        raise ValueError('All three verified candidate pairs are required; draft retained')
    work.mkdir(parents=True, exist_ok=False)
    inputs = work / 'inputs'
    for target in TARGETS:
        directory = inputs / target
        directory.mkdir(parents=True)
        for remote, local in [(f'qa-parser-{target}-candidate.zip', f'qa-parser-{target}-candidate.zip'),
                              (f'release-metadata-{target}.json', 'release-metadata.json')]:
            path = directory / local
            api(f"repos/{repo}/releases/assets/{current[remote]['id']}", output=path)
            check_asset(path, current[remote])
    out = work / 'assembled'
    if saved['mode'] == 'tag':
        (assembler or assemble)(inputs, out, saved['tag'], owner['commit'])
    else:
        checked = validate_candidates(inputs, owner['commit'])
        out.mkdir()
        rows = [{'target': target, 'filename': archive.name, 'sha256': sha(archive),
                 'bytes': archive.stat().st_size, 'evidence': data} for target, archive, data in checked]
        manifest = {'schema_version': 1, 'tag': saved['tag'], 'commit': owner['commit'],
                    'prerelease': True, 'draft': True, 'artifacts': rows}
        (out / 'release-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        (out / 'SHA256SUMS.txt').write_text(''.join(f"{r['sha256']}  {r['filename']}\n" for r in rows), encoding='utf-8')
        (out / 'RELEASE_NOTES.md').write_text(
            f"# 题库转换验证候选\n\n源码：{owner['commit']}；工作流：{owner['run_id']}。\n\n"
            "Windows x64、Mac ARM64、Mac x86_64原生构建及合成冻结程序检查均通过。"
            "完整解压对应ZIP，Windows双击EXE，Mac打开.app；保留运行时与许可证。\n\n"
            "此为手动草稿，不指定正式版本，不自动发布。真实9份题库未上传，不声称跨平台900/900；"
            "客户环境、签名公证和目标系统导入仍未验证。云端轻量版不含可选LibreOffice。\n",
            encoding='utf-8')
    files = sorted(p for p in out.iterdir() if p.is_file())
    expected = candidate_names | {p.name for p in files}
    if set(current) - expected:
        raise ValueError('Unexpected assets in owned draft; will not publish')
    for path in files:
        upload(repo, release_id, owner, path)
    remote = assets(repo, release_id)
    if set(remote) != expected:
        raise ValueError('Final asset set incomplete or unexpected; draft retained')
    for path in files:
        check_asset(path, remote[path.name])
    for target in TARGETS:
        directory = inputs / target
        check_asset(directory / f'qa-parser-{target}-candidate.zip', remote[f'qa-parser-{target}-candidate.zip'])
        check_asset(directory / 'release-metadata.json', remote[f'release-metadata-{target}.json'])
    owned(repo, release_id, owner)
    # Marker persists so failed finalization can only resume this exact run.
    body = marker(saved) + '\n\n' + (out / 'RELEASE_NOTES.md').read_text(encoding='utf-8')
    manifest = json.loads((out / 'release-manifest.json').read_text(encoding='utf-8'))
    publish = saved['mode'] == 'tag' and not keep_draft
    if saved['mode'] == 'tag':
        # Ensure the original user tag is still present immediately before publish.
        check_tag_commit(repo, saved['tag'], owner['commit'])
    result = api(f'repos/{repo}/releases/{release_id}', method='PATCH', payload={
        'body': body, 'tag_name': saved['tag'], 'target_commitish': owner['commit'],
        'draft': not publish, 'prerelease': manifest['prerelease'], 'make_latest': 'false'})
    if result.get('tag_name') != saved['tag'] or result.get('target_commitish') != owner['commit']:
        raise ValueError('Final release tag/source identity changed')
    if bool(result['draft']) == publish:
        raise ValueError('Final release state differs from requested state')
    print(json.dumps({'url': result['html_url'], 'draft': result['draft'], 'assets': len(remote)}))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
            stream.write(f"[{'完整验证草稿' if not publish else '版本发布'}]({result['html_url']})：三个原生目标检查及资产SHA校验通过。\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['prepare', 'upload', 'finalize'])
    parser.add_argument('--repo', default=os.environ.get('GITHUB_REPOSITORY'))
    parser.add_argument('--run-id', default=os.environ.get('GITHUB_RUN_ID'))
    parser.add_argument('--commit', default=os.environ.get('GITHUB_SHA'))
    parser.add_argument('--release-id')
    parser.add_argument('--mode', choices=['manual', 'tag'], default='manual')
    parser.add_argument('--tag', default='')
    parser.add_argument('--target', choices=TARGETS)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--metadata', type=Path, default=Path('dist/release-metadata.json'))
    parser.add_argument('--work', type=Path, default=Path('build/release-finalization'))
    args = parser.parse_args()
    owner = identity(args.repo or '', args.run_id or '', args.commit or '')
    if args.operation == 'prepare':
        prepare(args.repo, owner, args.mode, args.tag)
    elif args.operation == 'upload':
        if args.archive is None:
            parser.error('--archive is required for upload')
        upload_candidate(args.repo, args.release_id, owner, args.target, args.archive, args.metadata)
    else:
        finalize(args.repo, args.release_id, owner, args.work)


if __name__ == '__main__':
    main()
