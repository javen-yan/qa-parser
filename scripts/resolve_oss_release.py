#!/usr/bin/env python3
"""Resolve a completed trusted native run to its exact owned candidate Release."""
import json
import os
from pathlib import Path
import re
from release_draft import api, PREFIX
from sync_release_oss import release_owner, TARGETS


def resolve(repo, run_id, commit):
    run = api(f'repos/{repo}/actions/runs/{run_id}')
    if not (run.get('status') == 'completed' and run.get('conclusion') == 'success'
            and ((run.get('event') == 'workflow_dispatch' and run.get('head_branch') == 'main') or (run.get('event') == 'push' and re.fullmatch(r'v\d+\.\d+\.\d+(?:-(?:rc|beta|alpha)\.\d+)?', run.get('head_branch', ''))))
            and run.get('head_sha') == commit and run.get('head_repository', {}).get('full_name') == repo
            and run.get('path') == '.github/workflows/release.yml'):
        raise ValueError('Only a successful native workflow dispatched from this repository main is trusted')
    matches = []
    for page in range(1, 11):
        rows = api(f'repos/{repo}/releases?per_page=100&page={page}')
        for release in rows:
            markers = re.findall(re.escape(PREFIX) + r'(.*?) -->', release.get('body') or '')
            if len(markers) != 1:
                continue
            owner = json.loads(markers[0])
            if owner.get('run_id') == str(run_id) and owner.get('commit') == commit:
                release_owner(repo, dict(release, draft=True))
                matches.append(release)
        if len(rows) < 100:
            break
    if len(matches) != 1:
        raise ValueError('Expected exactly one owned candidate for this successful run')
    release = matches[0]
    archived = not release['draft'] and f"<!-- qa-parser-oss-delivery:{release['id']}:{commit} -->" in release.get('body', '')
    owner, _ = release_owner(repo, dict(release, draft=True))
    if run['event'] == 'push' and (owner['mode'] != 'tag' or owner['tag'] != run['head_branch']):
        raise ValueError('Tag-triggered run must resolve the exact version Release')
    if not release['draft'] and not archived and owner['mode'] != 'tag':
        raise ValueError('Candidate is published without this tool mirror record')
    return {'release_id': str(release['id']), 'skip': 'true' if archived else 'false'}


def main():
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    if os.environ['GITHUB_EVENT_NAME'] == 'workflow_run':
        run = event['workflow_run']
        result = resolve(os.environ['GITHUB_REPOSITORY'], run['id'], run['head_sha'])
        result.update(target='all', plan_only='false', cleanup_draft='true')
    else:
        values = event['inputs']
        if not re.fullmatch(r'[1-9][0-9]*', values['release_id']) or values['target'] not in (*TARGETS, 'all'):
            raise ValueError('Invalid explicit candidate selection')
        result = {'release_id': values['release_id'], 'target': values['target'], 'skip': 'false'}
        for key in ('plan_only', 'cleanup_draft'):
            value = str(values.get(key, True)).lower()
            if value not in ('true', 'false'):
                raise ValueError('Invalid boolean selection')
            result[key] = value
    with open(os.environ['GITHUB_OUTPUT'], 'a') as out:
        out.write(''.join(f'{key}={value}\n' for key, value in result.items()))
    print(json.dumps(result))

if __name__ == '__main__':
    main()
