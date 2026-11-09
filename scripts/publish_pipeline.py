#!/usr/bin/env python3
"""One resumable entry: existing native assets -> OSS -> release -> cleanup."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.error import HTTPError
from urllib.request import urlopen

import release_draft
from sync_release_oss import release_owner, settings, safe_error, main as sync
from cleanup_oss_draft import cleanup
from prune_releases import check_idle, prune, BASE
from verify_native_producer import verify


def source_assembler(commit, work):
    """Use the original tag's version, policy and notes, not newer main code."""
    snapshot = work / 'source-policy'
    for name in ('scripts/prepare_release.py', 'pyproject.toml', 'packaging/release-policy.json'):
        result = subprocess.run(['git', 'show', f'{commit}:{name}'], check=True, capture_output=True)
        dest = snapshot / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(result.stdout)
    spec = importlib.util.spec_from_file_location('verified_source_assembler', snapshot / 'scripts/prepare_release.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.assemble


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-id', required=True)
    parser.add_argument('--retire-ids', default='')
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    repo = os.environ.get('GITHUB_REPOSITORY', 'javen-yan/qa-parser')
    if repo != 'javen-yan/qa-parser' or not re.fullmatch(r'[1-9][0-9]*', args.release_id):
        raise ValueError('Only this repository and an explicit numeric Release are allowed')
    retire = set(args.retire_ids.split(',')) if args.retire_ids else set()
    if any(not re.fullmatch(r'[1-9][0-9]*', x) for x in retire) or args.release_id in retire:
        raise ValueError('Retirement requires distinct explicit historical Release IDs')
    settings(os.environ)  # ensure existing Secrets before mutations
    args.work.mkdir(parents=True, exist_ok=False)
    args.evidence.mkdir(parents=True, exist_ok=True)
    check_idle()
    release = release_draft.api(f'repos/{repo}/releases/{args.release_id}')
    owner, prefix = release_owner(repo, dict(release, draft=True))
    evidence = verify(repo, owner, active_run=os.environ.get('GITHUB_RUN_ID'))
    (args.evidence / 'native-producer.json').write_text(json.dumps(evidence, indent=2))
    if owner['mode'] == 'tag':
        release_draft.check_tag_commit(repo, owner['tag'], owner['commit'])
    if retire and owner['mode'] != 'tag':
        raise ValueError('Only a formal release can retire historical releases')
    receipt_url = BASE + prefix + '/release/delivery-record-' + '-'.join(release_draft.TARGETS) + '.json'
    receipt = None
    try:
        with urlopen(receipt_url, timeout=60) as response:
            receipt = json.load(response)
    except HTTPError as error:
        if error.code != 404:
            raise
    if receipt is None:
        if release['draft']:
            release_draft.finalize(repo, args.release_id,
                release_draft.identity(repo, str(owner['run_id']), owner['commit']), args.work / 'finalize',
                keep_draft=True, assembler=source_assembler(owner['commit'], args.work))
        sync(['--repo', repo, '--release-id', args.release_id, '--target', 'all', '--work', str(args.work / 'oss')])
        receipt = json.loads((args.work / 'oss/delivery-record.json').read_text())
    (args.evidence / 'delivery-record.json').write_text(json.dumps(receipt, indent=2))
    archived = cleanup(repo, args.release_id, receipt, apply=True, verify_native_jobs=True)
    (args.evidence / 'release-archive-result.json').write_text(json.dumps(archived, indent=2))
    retired = None
    if retire:
        retired = prune(args.release_id, retire, receipt, apply=True,
                        report_path=args.evidence / 'release-prune-report.json')
    result = {'status': 'delivered', 'release': archived, 'retirement': retired,
              'producer_evidence': evidence, 'receipt_url': receipt_url}
    (args.evidence / 'result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
            stream.write(f"\nRelease: {archived['release_url']}\n\nReceipt: {receipt_url}\n")


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Release delivery stopped: ' + safe_error(error), file=sys.stderr)
        raise SystemExit(1)
