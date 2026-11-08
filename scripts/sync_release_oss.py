#!/usr/bin/env python3
"""Opt-in immutable OSS distribution of verified Release binaries; no artifacts."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
from zipfile import ZipFile

from release_draft import api, assets, check_asset, identity, PREFIX, TARGETS, sha

SECRET_NAMES = tuple('ALIYUN_OSS_' + name for name in (
    'ACCESS_KEY_ID', 'ACCESS_KEY_SECRET', 'BUCKET', 'ENDPOINT', 'PUBLIC_BASE_URL', 'REGION'))


def settings(env):
    missing = [name for name in SECRET_NAMES if not env.get(name, '').strip()]
    if missing:
        raise ValueError('Missing repository Secrets: ' + ', '.join(missing))
    values = {name.removeprefix('ALIYUN_OSS_').lower(): env[name].strip() for name in SECRET_NAMES}
    for key in ('endpoint', 'public_base_url'):
        parsed = urlsplit(values[key])
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(f'{key} must be a plain HTTPS URL without credentials/query/fragment')
    endpoint = urlsplit(values['endpoint'])
    if not endpoint.hostname.endswith('.aliyuncs.com') or endpoint.path not in ('', '/'):
        raise ValueError('endpoint must be a standard OSS HTTPS endpoint')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]', values['bucket']):
        raise ValueError('Invalid OSS bucket name')
    if not re.fullmatch(r'[a-z0-9-]+', values['region']):
        raise ValueError('Invalid OSS region')
    values['public_base_url'] = values['public_base_url'].rstrip('/')
    return values


def release_owner(repo, release):
    matches = re.findall(re.escape(PREFIX) + r'(.*?) -->', release.get('body') or '')
    if len(matches) != 1:
        raise ValueError('Release ownership marker missing or ambiguous')
    owner = json.loads(matches[0])
    expected = identity(repo, owner.get('run_id', ''), owner.get('commit', ''))
    if any(owner.get(k) != v for k, v in expected.items()):
        raise ValueError('Release repository/source identity mismatch')
    tag = release.get('tag_name', '')
    if owner.get('tag') != tag or release.get('target_commitish') != owner['commit']:
        raise ValueError('Release tag/source identity changed')
    if release.get('draft'):
        if owner.get('mode') != 'manual' or tag != f"candidate-{owner['commit'][:12]}-{owner['run_id']}":
            raise ValueError('Only run-owned manual candidates can sync from a draft')
        channel = 'candidates'
    else:
        if owner.get('mode') != 'tag' or not re.fullmatch(r'v\d+\.\d+\.\d+(?:-(?:rc|beta|alpha)\.\d+)?', tag):
            raise ValueError('Published release must have a validated user version tag')
        channel = 'releases'
    return owner, f'qa-parser/{channel}/{tag}'


def download(repo, item, path):
    api(f"repos/{repo}/releases/assets/{item['id']}", output=path)
    check_asset(path, item)


def prepare(repo, release_id, targets, work, *, plan_only=False):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('Invalid repository')
    if not re.fullmatch(r'[1-9][0-9]*', str(release_id)):
        raise ValueError('release-id must be numeric')
    if not targets or any(target not in TARGETS for target in targets):
        raise ValueError('Unknown target')
    release = api(f'repos/{repo}/releases/{release_id}')
    owner, prefix = release_owner(repo, release)
    current = assets(repo, release_id)
    # Candidate archives have a fixed name; never enumerate arbitrary assets.
    for target in targets:
        for name in (f'qa-parser-{target}-candidate.zip', f'release-metadata-{target}.json'):
            if name not in current:
                raise ValueError(f'Verified target asset missing: {name}')
    work.mkdir(parents=True, exist_ok=False)
    if not release['draft']:
        if 'release-manifest.json' not in current:
            raise ValueError('Published release lacks completed native manifest')
        path = work / 'release-manifest.json'
        download(repo, current[path.name], path)
        manifest = json.loads(path.read_text(encoding='utf-8'))
        if manifest.get('commit') != owner['commit'] or manifest.get('tag') != owner['tag'] or {r['target'] for r in manifest.get('artifacts', [])} != set(TARGETS):
            raise ValueError('Published release native manifest is incomplete')
    release_files = []
    for name in ('release-manifest.json', 'SHA256SUMS.txt', 'RELEASE_NOTES.md'):
        if name in current:
            path = work / name
            if not path.exists():
                download(repo, current[name], path)
            release_files.append(str(path))
    rows = []
    for target in targets:
        folder = work / target
        folder.mkdir()
        meta_name = f'release-metadata-{target}.json'
        metadata = folder / meta_name
        download(repo, current[meta_name], metadata)
        data = json.loads(metadata.read_text(encoding='utf-8'))
        name = f'qa-parser-{target}-candidate.zip'
        item = current[name]
        if not (data.get('status') == 'passed' and data.get('target') == target and data.get('commit') == owner['commit']
                and data.get('native_smoke_passed') and data.get('source_closure_complete') and data.get('variant') == 'light'
                and item.get('state') == 'uploaded' and item.get('digest') == 'sha256:' + data.get('zip_sha256', '')
                and item.get('size') == data.get('bytes')):
            raise ValueError('Candidate metadata, source or server checksum gate failed')
        filename = name if release['draft'] else f"qa-parser-{owner['tag']}-{target}-light.zip"
        archive = folder / filename
        if not plan_only:
            print(f'Downloading and verifying {target} from Release {release_id}', flush=True)
            download(repo, item, archive)
            with ZipFile(archive) as z:
                if z.testzip() is not None:
                    raise ValueError('Release archive CRC check failed')
        checksum = folder / 'SHA256SUMS.txt'
        checksum.write_text(f"{data['zip_sha256']}  {filename}\n", encoding='utf-8')
        notes = folder / 'README.txt'
        notes.write_text(f"题库转换 {owner['tag']} / {target}\n源码：{owner['commit']}\nRelease：{release['html_url']}\n"
                         '完整解压后运行，Windows 双击 EXE，Mac 打开 .app；保留运行时与许可证。\n'
                         '原生合成检查已通过；真实题库、客户电脑、签名公证及目标系统导入未验收。\n'
                         + ('此为候选草稿，不是正式版。\n' if release['draft'] else ''), encoding='utf-8')
        rows.append({'target': target, 'prefix': prefix + '/' + target, 'filename': filename,
                     'sha256': data['zip_sha256'], 'bytes': data['bytes'],
                     'files': [str(p) for p in (archive, metadata, checksum, notes)]})
    return {'schema_version': 1, 'status': 'planned' if plan_only else 'downloaded-and-verified',
            'repository': repo, 'release_id': str(release_id), 'release_url': release['html_url'],
            'draft': release['draft'], 'commit': owner['commit'], 'run_id': owner['run_id'],
            'tag': owner['tag'], 'release_prefix': prefix + '/release', 'release_files': release_files, 'targets': rows}


def abort_stale_uploads(bucket, key):
    # Called only inside the Release-ID serialized CI job, before creating a
    # new upload. Never list broadly or abort prefix-neighbour objects.
    result = bucket.list_multipart_uploads(prefix=key, max_uploads=1000)
    if result.is_truncated:
        raise ValueError('Too many incomplete uploads; no fragment cleanup performed')
    for item in result.upload_list:
        if item.key == key and item.upload_id:
            bucket.abort_multipart_upload(key, item.upload_id)
            print(f'Aborted incomplete prior upload for {key}', flush=True)


def upload_multipart(bucket, key, path, headers, *, part_size=8 * 1024 * 1024, workers=4, part_info=None):
    if part_info is None:
        from oss2.models import PartInfo
        part_info = PartInfo
    init = bucket.init_multipart_upload(key, headers=headers)
    upload_id = init.upload_id
    parts = []
    def part(number, offset):
        with path.open('rb') as stream:
            stream.seek(offset)
            data = stream.read(part_size)
        result = bucket.upload_part(key, upload_id, number, data, headers={
            'Content-MD5': base64.b64encode(hashlib.md5(data).digest()).decode('ascii')})
        if result.status != 200 or result.crc is None:
            raise ValueError('Multipart part lacks successful CRC evidence')
        print(f'Uploaded verified part {number} ({len(data)} bytes)', flush=True)
        return part_info(number, result.etag, size=len(data), part_crc=result.crc)
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(part, number, offset) for number, offset in
                       enumerate(range(0, path.stat().st_size, part_size), 1)]
            try:
                for future in as_completed(futures):
                    parts.append(future.result())
            except BaseException:
                for future in futures:
                    future.cancel()
                raise
        # The SDK combines the per-part CRCs (with their sizes) and checks the
        # returned object CRC. The completion gate also forbids overwriting.
        return bucket.complete_multipart_upload(key, upload_id,
            sorted(parts, key=lambda p: p.part_number), headers={'x-oss-forbid-overwrite': 'true'})
    except BaseException:
        try:
            bucket.abort_multipart_upload(key, upload_id)
        except Exception as error:
            print('Own multipart abort failed: ' + safe_error(error), flush=True)
        raise


def upload_immutable(bucket, key, path):
    if not re.fullmatch(r'qa-parser/(?:candidates|releases)/[A-Za-z0-9.-]+/(?:windows-x64|macos-arm64|macos-x86_64|release)/[A-Za-z0-9_.-]+', key):
        raise ValueError('Unsafe OSS destination; no latest or arbitrary prefix allowed')
    digest = sha(path)
    try:
        head = bucket.head_object(key)
    except Exception as error:
        if getattr(error, 'status', None) != 404:
            raise
    else:
        headers = {k.lower(): str(v) for k, v in head.headers.items()}
        if int(head.content_length) != path.stat().st_size or headers.get('x-oss-meta-sha256') != digest:
            raise ValueError('OSS object already exists with different content; refusing overwrite')
        remote = bucket.get_object(key)
        try:
            check = hashlib.sha256()
            while chunk := remote.read(1024 * 1024):
                check.update(chunk)
            if check.hexdigest() != digest:
                raise ValueError('Existing OSS object failed actual content SHA-256')
        finally:
            remote.close()
        return 'already-identical'
    headers = {
        'x-oss-forbid-overwrite': 'true', 'x-oss-meta-sha256': digest,
        'Content-Type': 'application/zip' if path.suffix == '.zip' else 'text/plain; charset=utf-8',
        'Content-Disposition': f'attachment; filename="{path.name}"'}
    print(f'Uploading {key} ({path.stat().st_size} bytes)', flush=True)
    if path.stat().st_size > 16 * 1024 * 1024:
        if os.environ.get('GITHUB_ACTIONS') == 'true':
            abort_stale_uploads(bucket, key)
        result = upload_multipart(bucket, key, path, headers)
    else:
        with path.open('rb') as stream:
            md5 = hashlib.file_digest(stream, 'md5').digest()
        headers['Content-MD5'] = base64.b64encode(md5).decode('ascii')
        result = bucket.put_object_from_file(key, str(path), headers=headers)
    if result.status != 200 or result.crc is None:
        raise ValueError('OSS upload did not return successful CRC evidence')
    head = bucket.head_object(key)
    headers = {k.lower(): str(v) for k, v in head.headers.items()}
    if int(head.content_length) != path.stat().st_size or headers.get('x-oss-meta-sha256') != digest:
        raise ValueError('OSS uploaded object metadata/length mismatch')
    return 'uploaded'


def verify_public(url, size, digest=None):
    # No signed URL, access key, ACL mutation or public permission change.
    if digest:
        print(f'Verifying anonymous download SHA-256 ({size} bytes)', flush=True)
    with urlopen(Request(url, method='GET' if digest else 'HEAD'), timeout=60) as response:
        final = urlsplit(response.url)
        if final.scheme != 'https' or final.username or final.password or final.query or final.fragment:
            raise ValueError('Public download redirected to a credentialed/non-HTTPS URL')
        if response.status != 200 or int(response.headers.get('Content-Length', '-1')) != size:
            raise ValueError('Anonymous public download did not match uploaded object')
        if digest:
            check = hashlib.sha256()
            count = 0
            while chunk := response.read(1024 * 1024):
                count += len(chunk)
                check.update(chunk)
            if count != size or check.hexdigest() != digest:
                raise ValueError('Anonymous public download SHA-256 mismatch')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default=os.environ.get('GITHUB_REPOSITORY', 'javen-yan/qa-parser'))
    parser.add_argument('--release-id', required=True)
    parser.add_argument('--target', choices=(*TARGETS, 'all'), default='windows-x64')
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--plan-only', action='store_true')
    parser.add_argument('--cleanup-draft', action='store_true')
    args = parser.parse_args(argv)
    cfg = None if args.plan_only else settings(os.environ)
    targets = TARGETS if args.target == 'all' else (args.target,)
    report = prepare(args.repo, args.release_id, targets, args.work, plan_only=args.plan_only)
    if not args.plan_only:
        import oss2
        bucket = oss2.Bucket(oss2.AuthV4(cfg['access_key_id'], cfg['access_key_secret']),
                            cfg['endpoint'], cfg['bucket'], region=cfg['region'], enable_crc=True, connect_timeout=60)
        # OSS ignores forbid-overwrite when versioning is Enabled/Suspended.
        # Refuse that state; never change bucket configuration.
        if bucket.get_bucket_versioning().status not in (None, ''):
            raise ValueError('Bucket versioning prevents guaranteed forbid-overwrite; no writes performed')
        for row in report['targets']:
            row['uploads'] = []
            for filename in row.pop('files'):
                path = Path(filename)
                key = row['prefix'] + '/' + path.name
                state = upload_immutable(bucket, key, path)
                url = cfg['public_base_url'] + '/' + quote(key, safe='/')
                verify_public(url, path.stat().st_size, sha(path))
                row['uploads'].append({'key': key, 'url': url, 'bytes': path.stat().st_size, 'status': state, 'sha256': sha(path)})
            row['url'] = cfg['public_base_url'] + '/' + quote(row['prefix'] + '/' + row['filename'], safe='/')
            verify_public(row['url'], row['bytes'])
        report['release_uploads'] = []
        for filename in report.pop('release_files'):
            path = Path(filename)
            key = report['release_prefix'] + '/' + path.name
            upload_immutable(bucket, key, path)
            url = cfg['public_base_url'] + '/' + quote(key, safe='/')
            verify_public(url, path.stat().st_size, sha(path))
            report['release_uploads'].append({'key': key, 'url': url, 'bytes': path.stat().st_size, 'sha256': sha(path)})
        report['status'] = 'uploaded-and-public-sha256-verified'
        # Stable receipt is itself immutable and publicly verified before cleanup.
        # Retry-specific upload states are excluded from the persisted receipt.
        receipt = json.loads(json.dumps(report))
        for row in receipt['targets']:
            for item in row['uploads']:
                item.pop('status', None)
        receipt_path = args.work / 'delivery-record.json'
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
        record_key = report['release_prefix'] + '/delivery-record-' + '-'.join(targets) + '.json'
        upload_immutable(bucket, record_key, receipt_path)
        report['receipt_url'] = cfg['public_base_url'] + '/' + quote(record_key, safe='/')
        verify_public(report['receipt_url'], receipt_path.stat().st_size, sha(receipt_path))
        if args.cleanup_draft:
            from cleanup_oss_draft import cleanup
            report['cleanup'] = cleanup(args.repo, args.release_id, receipt, apply=True)
    (args.work / 'oss-sync-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
            stream.write(f"OSS: {report['status']}\n\n")
            for row in report['targets']:
                stream.write(f"- {row['target']}: {row.get('url', row['prefix'])}\n")
    return 0


def safe_error(error):
    message = str(error) if isinstance(error, ValueError) else type(error).__name__
    status = getattr(error, 'status', None)
    code = getattr(error, 'code', None)
    if isinstance(status, int):
        message += f' HTTP {status}'
    if isinstance(code, str) and re.fullmatch(r'[A-Za-z0-9_]{1,80}', code):
        message += ' ' + code
    if isinstance(error, RuntimeError):
        match = re.search(r'HTTP ([1-5][0-9]{2})', str(error))
        if match:
            message += ' GitHub HTTP ' + match[1]
    for name in SECRET_NAMES:
        value = os.environ.get(name)
        if value:
            message = message.replace(value, '[redacted]')
    return message


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        # SDK error payloads may include signed request details. Never print
        # their bodies, request headers, credentials or tracebacks.
        print('OSS sync failed: ' + safe_error(error), file=sys.stderr)
        raise SystemExit(1)
