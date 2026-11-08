#!/usr/bin/env python3
"""One explicitly selected formal Release; retire only an exact historical set.

Run under the same GitHub concurrency group as native and OSS workflows.
Never deletes git refs, source, OSS objects or another repository's data.
"""
import argparse
import json
import os
from pathlib import Path
import re
from urllib.request import urlopen
from release_draft import api, PREFIX, TARGETS
from sync_release_oss import release_owner, verify_public
from cleanup_oss_draft import read_api, asset_identity

REPO = 'javen-yan/qa-parser'
BASE = 'https://maiya-reader.oss-cn-beijing.aliyuncs.com/'
PRODUCERS = {'.github/workflows/release.yml', '.github/workflows/oss-sync.yml', '.github/workflows/prune-releases.yml'}


def inventory():
    result = []
    for page in range(1, 11):
        batch = read_api(f'repos/{REPO}/releases?per_page=100&page={page}')
        result.extend(batch)
        if len(batch) < 100:
            return {str(row['id']): row for row in result}
    raise ValueError('Release inventory exceeds bounded limit')


def check_idle():
    own = os.environ.get('GITHUB_RUN_ID')
    for status in ('in_progress', 'queued', 'waiting', 'requested', 'pending'):
        result = read_api(f'repos/{REPO}/actions/runs?status={status}&per_page=100')
        if result.get('total_count', 0) > 100:
            raise ValueError('Too many active workflows; no cleanup')
        if any(str(run['id']) != own and run.get('path') in PRODUCERS for run in result['workflow_runs']):
            raise ValueError('A production workflow is active or queued; no cleanup')


def source_tag(owner):
    obj = read_api(f"repos/{REPO}/git/ref/tags/{owner['tag']}")['object']
    for _ in range(8):
        if obj['type'] == 'commit':
            if obj['sha'] != owner['commit']:
                raise ValueError('Formal Source tag differs from delivered source')
            return
        if obj['type'] != 'tag':
            break
        obj = read_api(f"repos/{REPO}/git/tags/{obj['sha']}")['object']
    raise ValueError('Cannot resolve formal Source tag')


def signature(release):
    fields = ('id', 'tag_name', 'target_commitish', 'draft', 'prerelease', 'body')
    return {**{key: release.get(key) for key in fields},
            'assets': [asset_identity(item) for item in release.get('assets', [])]}


def prune(keep_id, expected_ids, receipt, *, apply=False, report_path=None):
    if not re.fullmatch(r'[1-9][0-9]*', keep_id) or not expected_ids or any(not re.fullmatch(r'[1-9][0-9]*', x) for x in expected_ids) or keep_id in expected_ids:
        raise ValueError('Expected one keep ID and an explicit distinct historical ID set')
    check_idle()
    before = inventory()
    if keep_id not in before or set(before) - ({keep_id} | set(expected_ids)):
        raise ValueError('Release inventory differs from the authorized set')
    keep = before[keep_id]
    owner, prefix = release_owner(REPO, keep)
    if owner['mode'] != 'tag' or keep['draft'] or keep['prerelease'] or keep.get('assets'):
        raise ValueError('Keep Release must be formal, published and OSS-only')
    marker = f"<!-- qa-parser-oss-delivery:{keep_id}:{owner['commit']} -->"
    if marker not in keep.get('body', ''):
        raise ValueError('Formal Release lacks verified OSS description')
    if not (receipt.get('status') == 'uploaded-and-public-sha256-verified' and receipt.get('repository') == REPO
            and str(receipt.get('release_id')) == keep_id and receipt.get('commit') == owner['commit']
            and receipt.get('tag') == owner['tag'] and {row['target'] for row in receipt['targets']} == set(TARGETS)):
        raise ValueError('Formal OSS receipt does not match all three native targets')
    source_tag(owner)
    objects = [item for row in receipt['targets'] for item in row['uploads']] + receipt.get('release_uploads', [])
    for item in objects:
        if not item['key'].startswith(prefix + '/') or item['url'] != BASE + item['key'] or item['url'] not in keep['body']:
            raise ValueError('OSS evidence or published download link differs')
        verify_public(item['url'], item['bytes'], item['sha256'])
    for old_id, old in before.items():
        if old_id == keep_id:
            continue
        old_owner, _ = release_owner(REPO, dict(old, draft=True))
        if not (old['draft'] or old['prerelease']):
            # Only an explicitly selected, older, already mirrored formal
            # release may be retired after the new complete delivery passes.
            old_version = re.fullmatch(r'v(\d+)\.(\d+)\.(\d+)', old_owner['tag'])
            new_version = re.fullmatch(r'v(\d+)\.(\d+)\.(\d+)', owner['tag'])
            old_marker = f"<!-- qa-parser-oss-delivery:{old_id}:{old_owner['commit']} -->"
            if (old_id not in expected_ids or old_owner['mode'] != 'tag'
                    or not old_version or not new_version
                    or tuple(map(int, old_version.groups())) >= tuple(map(int, new_version.groups()))
                    or old.get('assets') or old_marker not in old.get('body', '')):
                raise ValueError('Historical formal Release is not an older verified OSS-only version')
            source_tag(old_owner)
        if read_api(f"repos/{REPO}/actions/runs/{old_owner['run_id']}").get('status') != 'completed':
            raise ValueError('A historical Release producer is still running')
    report = {'schema_version': 1, 'keep_id': keep_id, 'version': owner['tag'], 'source_commit': owner['commit'],
              'release_url': keep['html_url'], 'expected_ids': sorted(expected_ids),
              'already_absent': sorted(set(expected_ids) - set(before)), 'deleted': [], 'status': 'verified'}
    def save():
        if report_path:
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    save()
    if apply:
        for old_id in sorted(set(expected_ids) & set(before)):
            check_idle()
            now = inventory()
            if set(now) - ({keep_id} | set(expected_ids)) or signature(now[keep_id]) != signature(keep) or old_id not in now or signature(now[old_id]) != signature(before[old_id]):
                raise ValueError('Release state changed during cleanup; stopping')
            api(f'repos/{REPO}/releases/{old_id}', method='DELETE')
            report['deleted'].append({'id': old_id, 'tag': before[old_id]['tag_name'], 'attachments': len(before[old_id].get('assets', []))})
            print(f"Removed historical Release {old_id} ({before[old_id]['tag_name']}); git tag retained", flush=True)
            save()
        after = inventory()
        if set(after) != {keep_id} or signature(after[keep_id]) != signature(keep):
            raise ValueError('Final Release inventory is not exactly the verified formal Release')
        source_tag(owner)
        report['status'] = 'only-formal-release-retained'
        save()
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--keep-id', required=True)
    p.add_argument('--expected-ids', required=True)
    p.add_argument('--apply', action='store_true')
    p.add_argument('--report', type=Path, required=True)
    args = p.parse_args()
    if not re.fullmatch(r'[1-9][0-9]*', args.keep_id):
        raise ValueError('Invalid keep ID')
    keep = read_api(f'repos/{REPO}/releases/{args.keep_id}')
    owner, prefix = release_owner(REPO, keep)
    url = BASE + prefix + '/release/delivery-record-' + '-'.join(TARGETS) + '.json'
    with urlopen(url, timeout=60) as response:
        receipt = json.load(response)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    result = prune(args.keep_id, set(args.expected_ids.split(',')), receipt, apply=args.apply, report_path=args.report)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as out:
            out.write(f"[{result['version']}]({result['release_url']}): {result['status']}; deleted {len(result['deleted'])} historical Releases. Source tags retained.\n")

if __name__ == '__main__':
    main()
